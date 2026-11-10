from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctbFade(Strategy):
    METADATA = {
        "name": "SolPctbFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # ~4 hours expected swing
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999

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

        # Post-exit cooldown gate to prevent overtrading and clustering
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Bollinger Bands calculation for current bar and previous bar
        curr_mid, curr_up, curr_low = self._bollinger(closes, self.period, self.num_std)
        prev_mid, prev_up, prev_low = self._bollinger(closes[:-1], self.period, self.num_std)

        if curr_mid is None or prev_mid is None:
            return None

        band_width_curr = curr_up - curr_low
        band_width_prev = prev_up - prev_low
        if band_width_curr <= 0 or band_width_prev <= 0:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        pct_b_curr = (curr_close - curr_low) / band_width_curr
        pct_b_prev = (prev_close - prev_low) / band_width_prev

        # Regime & Crisis Filter: avoid catching knives during extreme market disruptions
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "crisis_score": crisis_score})
            return None

        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # In-position dynamic exits (mid-band touch or target exhaustion)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            if pos_dir == "long":
                if curr_close >= curr_mid:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={"reason": "long_hit_midband", "close": curr_close, "mid": curr_mid, "bars_held": bars_held}
                    )
            elif pos_dir == "short":
                if curr_close <= curr_mid:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={"reason": "short_hit_midband", "close": curr_close, "mid": curr_mid, "bars_held": bars_held}
                    )
            return None

        # Entry logic: failed band breakout (pierced outside on previous bar, closed back inside on current bar)
        # Long entry: prev bar fell below lower band (pct_b < 0.0), current bar closed back inside (pct_b >= 0.05)
        if pct_b_prev < 0.0 and pct_b_curr >= 0.05 and pct_b_curr < 0.45:
            # Avoid long entries during extreme fear panics
            if fg_index >= 18:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "pctb_lower_reclaim",
                        "pct_b_prev": round(pct_b_prev, 4),
                        "pct_b_curr": round(pct_b_curr, 4),
                        "close": curr_close,
                        "mid": round(curr_mid, 2),
                    }
                )

        # Short entry: prev bar broke above upper band (pct_b > 1.0), current bar closed back inside (pct_b <= 0.95)
        if pct_b_prev > 1.0 and pct_b_curr <= 0.95 and pct_b_curr > 0.55:
            # Avoid short entries during extreme greed runaway trends
            if fg_index <= 82:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "pctb_upper_rejection",
                        "pct_b_prev": round(pct_b_prev, 4),
                        "pct_b_curr": round(pct_b_curr, 4),
                        "close": curr_close,
                        "mid": round(curr_mid, 2),
                    }
                )

        return None