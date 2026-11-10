from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeEventReversion(Strategy):
    METADATA = {
        "name": "SolPctBFadeEventReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _bollinger_stats(self, closes: list, period: int = 20, num_std: float = 2.0):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        upper = mean + num_std * std
        lower = mean - num_std * std
        return mean, upper, lower

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 3)
        if len(closes) < self.period + 2:
            return None

        # Cooldown guard to maintain event-study precision and avoid friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        bb_curr = self._bollinger_stats(closes, self.period, self.num_std)
        bb_prev = self._bollinger_stats(closes[:-1], self.period, self.num_std)

        if not bb_curr or not bb_prev:
            return None

        mid_curr, up_curr, low_curr = bb_curr
        mid_prev, up_prev, low_prev = bb_prev

        band_width_curr = up_curr - low_curr
        band_width_prev = up_prev - low_prev

        if band_width_curr <= 1e-6 or band_width_prev <= 1e-6:
            return None

        pct_b_curr = (closes[-1] - low_curr) / band_width_curr
        pct_b_prev = (closes[-2] - low_prev) / band_width_prev

        # Active position management: Mid-band target exit
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and closes[-1] >= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_tp_mid_band_cross",
                        "price": closes[-1],
                        "mid": mid_curr,
                        "pct_b": pct_b_curr,
                    },
                )
            elif direction == "short" and closes[-1] <= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_tp_mid_band_cross",
                        "price": closes[-1],
                        "mid": mid_curr,
                        "pct_b": pct_b_curr,
                    },
                )
            return None

        # Avoid entries during structural market distress
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Event: Prior bar penetrated outside band (<0.0 or >1.0), current bar closed back inside
        long_reentry = (pct_b_prev < 0.0) and (pct_b_curr >= 0.05) and (pct_b_curr <= 0.45)
        short_reentry = (pct_b_prev > 1.0) and (pct_b_curr <= 0.95) and (pct_b_curr >= 0.55)

        if long_reentry:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_lower_penetration_reentry_long",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "price": closes[-1],
                    "lower_band": low_curr,
                    "mid_band": mid_curr,
                },
            )

        if short_reentry:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_upper_penetration_reentry_short",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "price": closes[-1],
                    "upper_band": up_curr,
                    "mid_band": mid_curr,
                },
            )

        return None