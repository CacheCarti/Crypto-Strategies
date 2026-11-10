from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeReversion(Strategy):
    METADATA = {
        "name": "SolPctBFadeReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -999

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
        closes = ctx.closes(self.bb_period + 2)
        lows = ctx.lows(2)
        highs = ctx.highs(2)

        if len(closes) < self.bb_period + 2:
            return None

        # Filter out extreme crisis regimes to prevent failing OOS trend bleed
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_regime_abort", "crisis_score": crisis_score}
                )
            return None

        # Calculate Bollinger Bands on the current bar
        mid, upper, lower = self._bollinger(closes, self.bb_period, self.num_std)
        if mid is None or upper is None or lower is None or upper == lower:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        bandwidth = (upper - lower) / mid
        # Avoid fading during dead/compressed volatility or runaway expansion
        if bandwidth < 0.015:
            return None

        pct_b = (current_close - lower) / (upper - lower)

        # In-position logic: mean reversion target hit at mid-band
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_close >= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_mid_band_target", "pct_b": pct_b, "mid": mid, "close": current_close}
                )
            elif pos_dir == "short" and current_close <= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_mid_band_target", "pct_b": pct_b, "mid": mid, "close": current_close}
                )
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Entry: Previous low punctured below lower band (or prev close < lower),
        # but current bar recaptures above the lower band (0.0 < pct_b < 0.40)
        pierced_lower = prev_low < lower or prev_close < lower
        recaptured_lower = current_close > lower and pct_b < 0.40

        if pierced_lower and recaptured_lower:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "pctb_lower_pierce_recapture",
                    "pct_b": pct_b,
                    "lower": lower,
                    "mid": mid,
                    "close": current_close,
                }
            )

        # Short Entry: Previous high punctured above upper band (or prev close > upper),
        # but current bar closes back inside below upper band (0.60 < pct_b < 1.0)
        pierced_upper = prev_high > upper or prev_close > upper
        recaptured_upper = current_close < upper and pct_b > 0.60

        if pierced_upper and recaptured_upper:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "pctb_upper_pierce_recapture",
                    "pct_b": pct_b,
                    "upper": upper,
                    "mid": mid,
                    "close": current_close,
                }
            )

        return None