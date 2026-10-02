from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class VolatilityExpansionMomentum(Strategy):
    METADATA = {
        "name": "Volatility Expansion Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 70,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.atr_ma_period = 50
        self.momentum_lookback = 12
        self.expansion_threshold = 1.50
        self.momentum_mult = 1.85
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.min_hold_bars = 3
        self.entry_bar = -999

    def _calc_atr_series(self, highs, lows, closes, period: int, count: int):
        needed = period + count
        if len(closes) < needed + 1:
            return None
        h = highs[-needed - 1 :]
        l = lows[-needed - 1 :]
        c = closes[-needed - 1 :]

        trs = []
        for i in range(1, len(c)):
            tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
            trs.append(tr)

        atr_series = []
        for i in range(period, len(trs) + 1):
            atr_series.append(sum(trs[i - period : i]) / period)
        return atr_series[-count:]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = self.atr_period + self.atr_ma_period + self.momentum_lookback + 5
        closes = ctx.closes(needed_bars)
        highs = ctx.highs(needed_bars)
        lows = ctx.lows(needed_bars)

        if len(closes) < needed_bars:
            return None

        atr_series = self._calc_atr_series(highs, lows, closes, self.atr_period, self.atr_ma_period)
        if not atr_series or len(atr_series) < self.atr_ma_period:
            return None

        current_atr = atr_series[-1]
        baseline_atr = sum(atr_series) / len(atr_series)
        if baseline_atr <= 0:
            return None

        atr_ratio = current_atr / baseline_atr
        price_now = closes[-1]
        price_prev = closes[-1 - self.momentum_lookback]
        net_move = price_now - price_prev
        move_threshold = self.momentum_mult * current_atr

        # Position Management & Exit Logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            should_exit = False
            exit_reason = ""

            # Only evaluate structural exits after a minimum hold to avoid micro-whipsaws
            if bars_held >= self.min_hold_bars:
                # Volatility has fully contracted back below baseline
                if atr_ratio < 0.92:
                    should_exit = True
                    exit_reason = "atr_contracted_below_baseline"
                # Clear adverse momentum reversal against open position
                elif pos_dir == "long" and net_move < -1.0 * current_atr:
                    should_exit = True
                    exit_reason = "momentum_reversed_bearish"
                elif pos_dir == "short" and net_move > 1.0 * current_atr:
                    should_exit = True
                    exit_reason = "momentum_reversed_bullish"

            if should_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": exit_reason,
                        "atr": round(current_atr, 2),
                        "baseline_atr": round(baseline_atr, 2),
                        "atr_ratio": round(atr_ratio, 3),
                        "net_move": round(net_move, 2),
                        "price": round(price_now, 2),
                        "bars_held": bars_held,
                    },
                )
            return None

        # Hard Cooldown Guard after exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Skip during extreme crisis to avoid unmodelled gap risk
        if ctx.market.get("regime", "NORMAL") in ("CRISIS", "MELTDOWN"):
            return None

        # Entry Logic: High-conviction volatility expansion with strong directional displacement
        is_expanding = atr_ratio >= self.expansion_threshold

        if is_expanding:
            confidence = min(0.92, 0.65 + 0.12 * (atr_ratio - self.expansion_threshold))

            # Bullish expansion breakout
            if net_move > move_threshold:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vol_expansion_bull_momentum",
                        "atr": round(current_atr, 2),
                        "baseline_atr": round(baseline_atr, 2),
                        "atr_ratio": round(atr_ratio, 3),
                        "net_move": round(net_move, 2),
                        "threshold": round(move_threshold, 2),
                        "price": round(price_now, 2),
                    },
                )

            # Bearish expansion breakdown
            elif net_move < -move_threshold:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vol_expansion_bear_momentum",
                        "atr": round(current_atr, 2),
                        "baseline_atr": round(baseline_atr, 2),
                        "atr_ratio": round(atr_ratio, 3),
                        "net_move": round(net_move, 2),
                        "threshold": round(move_threshold, 2),
                        "price": round(price_now, 2),
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index