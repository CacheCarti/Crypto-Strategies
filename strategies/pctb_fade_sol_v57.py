from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeEventStrategy(Strategy):
    METADATA = {
        "name": "SolPctBFadeEventStrategy",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std = 2.0
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _bollinger(self, closes):
        n = self.bb_period
        if len(closes) < n:
            return None, None, None
        slice_c = closes[-n:]
        mean = sum(slice_c) / n
        var = sum((x - mean) ** 2 for x in slice_c) / n
        std = math.sqrt(var)
        return mean, mean + self.bb_std * std, mean - self.bb_std * std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.bb_period + 2)
        if len(closes) < self.bb_period + 2:
            return None

        # Filter out crisis / meltdown regimes to avoid steamroller trends
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.45 or regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_exit", "regime": regime, "crisis_score": crisis_score}
                )
            return None

        # Compute Bollinger Bands for current and prior bar
        mean_curr, upper_curr, lower_curr = self._bollinger(closes[-self.bb_period:])
        mean_prev, upper_prev, lower_prev = self._bollinger(closes[-(self.bb_period + 1):-1])

        if upper_curr is None or upper_prev is None:
            return None

        bandwidth_curr = upper_curr - lower_curr
        bandwidth_prev = upper_prev - lower_prev
        if bandwidth_curr <= 1e-6 or bandwidth_prev <= 1e-6:
            return None

        c_curr = closes[-1]
        c_prev = closes[-2]

        pct_b_curr = (c_curr - lower_curr) / bandwidth_curr
        pct_b_prev = (c_prev - lower_prev) / bandwidth_prev

        # Active position management: Mid-band exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0

            # Long exit: cross back above the mid-band or max hold timeout
            if pos_dir == "long":
                if c_curr >= mean_curr:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={"reason": "target_mid_band_reached", "close": c_curr, "mid": mean_curr, "bars_held": bars_held}
                    )
                if bars_held >= 14:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "time_stop_exit", "close": c_curr, "bars_held": bars_held}
                    )

            # Short exit: cross back below the mid-band or max hold timeout
            elif pos_dir == "short":
                if c_curr <= mean_curr:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={"reason": "target_mid_band_reached", "close": c_curr, "mid": mean_curr, "bars_held": bars_held}
                    )
                if bars_held >= 14:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "time_stop_exit", "close": c_curr, "bars_held": bars_held}
                    )
            return None

        # Post-exit cooldown enforcement to control churn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Discrete Event: Failed breakout re-entry
        # Long Setup: Prior bar pierced below lower band (pct_b < 0.0), current bar closes back inside (0.0 <= pct_b <= 0.35)
        if pct_b_prev < 0.0 and (0.0 <= pct_b_curr <= 0.35):
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=320.0,
                take_profit_bps=400.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "failed_breakdown_reentry",
                    "pct_b_prev": round(pct_b_prev, 3),
                    "pct_b_curr": round(pct_b_curr, 3),
                    "close": round(c_curr, 2),
                    "lower_band": round(lower_curr, 2),
                    "mid_band": round(mean_curr, 2)
                }
            )

        # Short Setup: Prior bar pierced above upper band (pct_b > 1.0), current bar closes back inside (0.65 <= pct_b <= 1.0)
        if pct_b_prev > 1.0 and (0.65 <= pct_b_curr <= 1.0):
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=320.0,
                take_profit_bps=400.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "failed_breakout_reentry",
                    "pct_b_prev": round(pct_b_prev, 3),
                    "pct_b_curr": round(pct_b_curr, 3),
                    "close": round(c_curr, 2),
                    "upper_band": round(upper_curr, 2),
                    "mid_band": round(mean_curr, 2)
                }
            )

        return None