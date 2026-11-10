from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeReversion(Strategy):
    METADATA = {
        "name": "SolPctBFadeReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _bollinger(self, closes: list, period: int = 20, num_std: float = 2.0):
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
        closes = ctx.closes(self.period + 3)
        if len(closes) < self.period + 2:
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Bollinger Bands on previous bar and current bar
        prev_closes = closes[:-1]
        mid_prev, upper_prev, lower_prev = self._bollinger(prev_closes, self.period, self.num_std)
        mid_curr, upper_curr, lower_curr = self._bollinger(closes, self.period, self.num_std)

        if mid_prev is None or mid_curr is None:
            return None

        bw_prev = upper_prev - lower_prev
        bw_curr = upper_curr - lower_curr
        if bw_prev <= 0 or bw_curr <= 0:
            return None

        # Calculate %B
        pct_b_prev = (prev_closes[-1] - lower_prev) / bw_prev
        pct_b_curr = (closes[-1] - lower_curr) / bw_curr

        current_price = ctx.bar.close

        # Position management: Target exit at mid-band
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_price >= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "long_take_profit_mid_band",
                        "price": current_price,
                        "mid": mid_curr,
                        "pct_b": pct_b_curr,
                    },
                )
            elif pos_dir == "short" and current_price <= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "short_take_profit_mid_band",
                        "price": current_price,
                        "mid": mid_curr,
                        "pct_b": pct_b_curr,
                    },
                )
            return None

        # Market stress filter: avoid entering band breaks during extreme meltdown
        crisis = ctx.market.get("crisis_score", 0.0)
        if crisis > 0.65:
            return None

        # Filter extreme funding imbalances to avoid fading strong runaway trends
        funding = ctx.features.get("funding_rate_solusdt", 0.0)

        # Long Setup: Previous bar pierced below lower band (%B < 0), current bar closes back inside (%B > 0)
        if pct_b_prev < 0.0 and pct_b_curr >= 0.05 and pct_b_curr < 0.45:
            if funding > -0.0005:  # Avoid longs during catastrophic funding unwinds
                conf = min(0.9, 0.65 + abs(pct_b_prev) * 0.5)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "pctb_lower_rejection_reentry",
                        "pct_b_prev": round(pct_b_prev, 4),
                        "pct_b_curr": round(pct_b_curr, 4),
                        "lower_band": round(lower_curr, 2),
                        "mid_band": round(mid_curr, 2),
                        "funding": funding,
                    },
                )

        # Short Setup: Previous bar pierced above upper band (%B > 1), current bar closes back inside (%B < 1)
        if pct_b_prev > 1.0 and pct_b_curr <= 0.95 and pct_b_curr > 0.55:
            if funding < 0.0005:  # Avoid shorts during massive spot squeeze/funding rallies
                conf = min(0.9, 0.65 + (pct_b_prev - 1.0) * 0.5)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "pctb_upper_rejection_reentry",
                        "pct_b_prev": round(pct_b_prev, 4),
                        "pct_b_curr": round(pct_b_curr, 4),
                        "upper_band": round(upper_curr, 2),
                        "mid_band": round(mid_curr, 2),
                        "funding": funding,
                    },
                )

        return None