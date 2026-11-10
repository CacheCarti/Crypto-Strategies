from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 330.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.atr_period = 14
        self.min_wick_atr_mult = 0.25
        self.cooldown_bars = 6
        self.max_hold_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_bars = self.lookback + self.atr_period + 2
        closes = ctx.closes(total_bars)
        if len(closes) < total_bars:
            return None

        highs = ctx.highs(total_bars)
        lows = ctx.lows(total_bars)

        # Time-based exit for open positions
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_exhaustion", "bars_held": bars_held}
                )
            return None

        # Mandatory post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime Gate: Prior evidence shows naive breakout fading fails in trending/crisis markets.
        # Restrict fading to non-crisis and non-extreme trending regimes.
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        trend_regime = ctx.market.get("trend_regime", "neutral")

        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.35:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # 20-bar range excluding current bar
        prev_highs = highs[-(self.lookback + 1):-1]
        prev_lows = lows[-(self.lookback + 1):-1]
        range_high = max(prev_highs)
        range_low = min(prev_lows)

        curr_high = highs[-1]
        curr_low = lows[-1]
        curr_close = closes[-1]
        min_penetration = atr * self.min_wick_atr_mult

        # Bear Trap / False Breakdown -> Go Long
        # Intrabar poke below range_low by at least min_penetration, but close finishes back inside
        if curr_low < (range_low - min_penetration) and curr_close > range_low:
            if trend_regime != "bear":  # Do not fade breakdowns in persistent bear trends
                wick_size = range_low - curr_low
                sl_bps = max(100.0, min(300.0, (wick_size / curr_close) * 10000.0 * 1.25))
                tp_bps = sl_bps * 1.5
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "failed_breakdown_bear_trap",
                        "range_low": range_low,
                        "wick_low": curr_low,
                        "close": curr_close,
                        "atr": atr,
                        "regime": regime
                    }
                )

        # Bull Trap / False Breakout -> Go Short
        # Intrabar poke above range_high by at least min_penetration, but close finishes back inside
        if curr_high > (range_high + min_penetration) and curr_close < range_high:
            if trend_regime != "bull":  # Do not fade breakouts in persistent bull trends
                wick_size = curr_high - range_high
                sl_bps = max(100.0, min(300.0, (wick_size / curr_close) * 10000.0 * 1.25))
                tp_bps = sl_bps * 1.5
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "failed_breakout_bull_trap",
                        "range_high": range_high,
                        "wick_high": curr_high,
                        "close": curr_close,
                        "atr": atr,
                        "regime": regime
                    }
                )

        return None