from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBReversion(Strategy):
    METADATA = {
        "name": "SolPctBReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _bollinger_bands(self, closes):
        if len(closes) < self.period:
            return None, None, None
        window = closes[-self.period:]
        sma = sum(window) / self.period
        variance = sum((x - sma) ** 2 for x in window) / self.period
        std = math.sqrt(variance)
        upper = sma + self.num_std * std
        lower = sma - self.num_std * std
        return lower, sma, upper

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 2)
        if len(closes) < self.period + 2:
            return None

        # Calculate bands for previous bar and current bar
        prev_closes = closes[:-1]
        lower_prev, mid_prev, upper_prev = self._bollinger_bands(prev_closes)
        lower_curr, mid_curr, upper_curr = self._bollinger_bands(closes)

        if lower_prev is None or lower_curr is None:
            return None

        # Avoid zero division
        band_width_prev = upper_prev - lower_prev
        band_width_curr = upper_curr - lower_curr
        if band_width_prev <= 0 or band_width_curr <= 0:
            return None

        pct_b_prev = (closes[-2] - lower_prev) / band_width_prev
        pct_b_curr = (closes[-1] - lower_curr) / band_width_curr

        # Handle active position exit at mid-band
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and closes[-1] >= mid_curr:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_midband_reversion_long",
                    "close": closes[-1],
                    "mid_band": mid_curr,
                    "pct_b": pct_b_curr
                })
            elif pos_dir == "short" and closes[-1] <= mid_curr:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_midband_reversion_short",
                    "close": closes[-1],
                    "mid_band": mid_curr,
                    "pct_b": pct_b_curr
                })
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate to fix prior alpha/oos breakdown:
        # Avoid fading band breaks during extreme market meltdown / crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.45 or regime == "MELTDOWN":
            return None

        # Entry logic: failed breakout re-entering the band
        # Long: previous bar closed below lower band (%B < 0), current bar closed back inside (%B >= 0 and %B < 0.40)
        if pct_b_prev < 0.0 and 0.0 <= pct_b_curr < 0.40:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pct_b_failed_lower_break_bounce",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "lower_band": lower_curr,
                    "mid_band": mid_curr
                }
            )

        # Short: previous bar closed above upper band (%B > 1.0), current bar closed back inside (%B <= 1.0 and %B > 0.60)
        if pct_b_prev > 1.0 and 0.60 < pct_b_curr <= 1.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pct_b_failed_upper_break_fade",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "upper_band": upper_curr,
                    "mid_band": mid_curr
                }
            )

        return None