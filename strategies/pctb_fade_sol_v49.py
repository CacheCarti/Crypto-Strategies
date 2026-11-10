from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeReversion(Strategy):
    METADATA = {
        "name": "SolPctBFadeReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std = 2.0
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _bollinger(self, closes, period=20, num_std=2.0):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.bb_period + 3)
        if len(closes) < self.bb_period + 2:
            return None

        # Compute bands for previous bar and current bar to capture exact boundary re-entry
        bb_prev = self._bollinger(closes[:-1], self.bb_period, self.bb_std)
        bb_curr = self._bollinger(closes, self.bb_period, self.bb_std)

        if bb_prev is None or bb_curr is None:
            return None

        mid_prev, upper_prev, lower_prev = bb_prev
        mid_curr, upper_curr, lower_curr = bb_curr

        prev_width = upper_prev - lower_prev
        curr_width = upper_curr - lower_curr
        if prev_width <= 0 or curr_width <= 0:
            return None

        pct_b_prev = (closes[-2] - lower_prev) / prev_width
        pct_b_curr = (closes[-1] - lower_curr) / curr_width

        # Check open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and closes[-1] >= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_tp_midband",
                        "pct_b": round(pct_b_curr, 3),
                        "close": closes[-1],
                        "mid": round(mid_curr, 2),
                    }
                )
            elif direction == "short" and closes[-1] <= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_tp_midband",
                        "pct_b": round(pct_b_curr, 3),
                        "close": closes[-1],
                        "mid": round(mid_curr, 2),
                    }
                )
            return None

        # Cooldown guard after position close
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime & Crisis safety filter
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Strict event boundary:
        # Long entry: Previous bar closed outside lower band (pct_b < 0.0), current closes back inside (0.05 <= pct_b <= 0.40)
        # Short entry: Previous bar closed outside upper band (pct_b > 1.0), current closes back inside (0.60 <= pct_b <= 0.95)
        if pct_b_prev < 0.0 and 0.05 <= pct_b_curr <= 0.40:
            confidence = min(0.85, 0.60 + max(0.0, -pct_b_prev) * 0.5)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_lower_reentry_fade",
                    "pct_b_prev": round(pct_b_prev, 3),
                    "pct_b_curr": round(pct_b_curr, 3),
                    "close": closes[-1],
                    "lower_band": round(lower_curr, 2),
                }
            )

        if pct_b_prev > 1.0 and 0.60 <= pct_b_curr <= 0.95:
            confidence = min(0.85, 0.60 + max(0.0, pct_b_prev - 1.0) * 0.5)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_upper_reentry_fade",
                    "pct_b_prev": round(pct_b_prev, 3),
                    "pct_b_curr": round(pct_b_curr, 3),
                    "close": closes[-1],
                    "upper_band": round(upper_curr, 2),
                }
            )

        return None