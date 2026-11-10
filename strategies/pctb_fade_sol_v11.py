from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFade(Strategy):
    METADATA = {
        "name": "SolPctBFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _bollinger(self, closes, period=20, num_std=2.0):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 2)
        if len(closes) < self.period + 2:
            return None

        # Calculate bands for previous bar
        prev_closes = closes[:-1]
        mid_prev, upper_prev, lower_prev = self._bollinger(prev_closes, self.period, self.num_std)
        # Calculate bands for current bar
        mid_curr, upper_curr, lower_curr = self._bollinger(closes, self.period, self.num_std)

        if mid_prev is None or mid_curr is None:
            return None

        bandwidth_prev = upper_prev - lower_prev
        bandwidth_curr = upper_curr - lower_curr
        if bandwidth_prev <= 0 or bandwidth_curr <= 0:
            return None

        prev_pct_b = (prev_closes[-1] - lower_prev) / bandwidth_prev
        curr_pct_b = (closes[-1] - lower_curr) / bandwidth_curr
        price = closes[-1]

        # Handle open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and price >= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_tp_midband", "price": price, "mid": mid_curr, "pct_b": curr_pct_b}
                )
            elif direction == "short" and price <= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_tp_midband", "price": price, "mid": mid_curr, "pct_b": curr_pct_b}
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate based on prior evidence: avoid Meltdown/Crisis regimes where breakout continuation kills mean-reversion
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            return None

        # Entry logic: failed band breaks (must close outside on previous bar, inside on current)
        long_setup = (prev_pct_b < 0.0) and (curr_pct_b >= 0.0) and (curr_pct_b < 0.35)
        short_setup = (prev_pct_b > 1.0) and (curr_pct_b <= 1.0) and (curr_pct_b > 0.65)

        if long_setup:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_lower_band_break",
                    "prev_pct_b": round(prev_pct_b, 4),
                    "curr_pct_b": round(curr_pct_b, 4),
                    "lower_curr": round(lower_curr, 2),
                    "mid_curr": round(mid_curr, 2),
                    "price": round(price, 2)
                }
            )

        if short_setup:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_upper_band_break",
                    "prev_pct_b": round(prev_pct_b, 4),
                    "curr_pct_b": round(curr_pct_b, 4),
                    "upper_curr": round(upper_curr, 2),
                    "mid_curr": round(mid_curr, 2),
                    "price": round(price, 2)
                }
            )

        return None