from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class VolExpansionMomentum(Strategy):
    METADATA = {
        "name": "VolExpansionMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 10
        self.atr_base_period = 35
        self.mom_lookback = 10
        self.expansion_mult = 1.35
        self.mom_mult = 1.4
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _calc_tr_series(self, highs, lows, closes):
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        return trs

    def _calc_atr_and_baseline(self, highs, lows, closes):
        trs = self._calc_tr_series(highs, lows, closes)
        total_req = self.atr_base_period + self.atr_period
        if len(trs) < total_req:
            return None, None

        atr_series = []
        for i in range(self.atr_base_period):
            end_idx = len(trs) - (self.atr_base_period - 1 - i)
            start_idx = end_idx - self.atr_period
            window_tr = trs[start_idx:end_idx]
            atr_series.append(sum(window_tr) / self.atr_period)

        current_atr = atr_series[-1]
        baseline_atr = sum(atr_series) / len(atr_series)
        return current_atr, baseline_atr

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = self.atr_base_period + self.atr_period + self.mom_lookback + 5
        closes = ctx.closes(req_bars)
        highs = ctx.highs(req_bars)
        lows = ctx.lows(req_bars)

        if len(closes) < req_bars:
            return None

        current_atr, baseline_atr = self._calc_atr_and_baseline(highs, lows, closes)
        if current_atr is None or baseline_atr is None or baseline_atr <= 0:
            return None

        atr_ratio = current_atr / baseline_atr
        current_price = closes[-1]
        past_price = closes[-1 - self.mom_lookback]
        net_move = current_price - past_price
        abs_move = abs(net_move)

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic for open position
        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar
            # Contraction exit: volatility contracted significantly below baseline
            # or momentum reversed against position
            is_contracted = atr_ratio < 0.95
            is_reversed = (pos_dir == "long" and net_move < -current_atr * 0.8) or (
                pos_dir == "short" and net_move > current_atr * 0.8
            )

            if is_contracted or is_reversed or bars_held > 18:
                reason = "atr_contraction" if is_contracted else ("mom_reversal" if is_reversed else "time_limit")
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": f"exit_{reason}",
                        "atr_ratio": round(atr_ratio, 3),
                        "net_move": round(net_move, 2),
                        "bars_held": bars_held,
                        "price": round(current_price, 2),
                    },
                )
            return None

        # Entry logic
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        if bars_since_exit < self.cooldown_bars:
            return None

        # Volatility expansion condition
        is_expanding = atr_ratio >= self.expansion_mult
        has_directional_momentum = abs_move >= (self.mom_mult * current_atr)

        if is_expanding and has_directional_momentum:
            confidence = min(0.5 + (atr_ratio - self.expansion_mult) * 0.4 + (abs_move / (current_atr * 3.0)) * 0.2, 0.95)

            if net_move > 0:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=round(confidence, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vol_expansion_bullish_momentum",
                        "atr": round(current_atr, 2),
                        "baseline_atr": round(baseline_atr, 2),
                        "atr_ratio": round(atr_ratio, 3),
                        "net_move": round(net_move, 2),
                        "price": round(current_price, 2),
                    },
                )
            elif net_move < 0:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=round(confidence, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vol_expansion_bearish_momentum",
                        "atr": round(current_atr, 2),
                        "baseline_atr": round(baseline_atr, 2),
                        "atr_ratio": round(atr_ratio, 3),
                        "net_move": round(net_move, 2),
                        "price": round(current_price, 2),
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index