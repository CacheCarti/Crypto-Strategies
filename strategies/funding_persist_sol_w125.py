from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolFundingFade(Strategy):
    METADATA = {
        "name": "SOL Funding Rate Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Aim for a few hours
        "warmup_bars": 50,  # Need enough history for funding persistence and EMAs
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 8  # Number of bars to check for persistent funding
        self.funding_threshold = 0.00015  # 0.015% per 8h, relatively high
        self.ema_short_period = 10
        self.ema_long_period = 30
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

        # Store recent funding rates to check for persistence
        self.funding_history = []

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_funding = ctx.features.get("funding_rate_solusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > self.funding_period:
            self.funding_history.pop(0)

        # Ensure we have enough funding history
        if len(self.funding_history) < self.funding_period:
            return None

        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)

        if ema_short is None or ema_long is None:
            return None

        signal_direction = None
        confidence = 0.0
        reason = "hold"
        metadata = {
            "current_funding": current_funding,
            "ema_short": ema_short,
            "ema_long": ema_long,
            "price": ctx.bar.close
        }

        # Check for persistent negative funding (long signal)
        persistent_negative_funding = all(f < -self.funding_threshold for f in self.funding_history)
        if persistent_negative_funding:
            # Price confirmation: current price is above EMA_short and EMA_short is above EMA_long
            # indicating a potential bounce or underlying bullishness despite negative funding
            if ctx.bar.close > ema_short and ema_short > ema_long:
                signal_direction = "long"
                confidence = min(0.7 + (abs(current_funding) / (self.funding_threshold * 2)), 0.9) # Scale confidence
                reason = "persistent_negative_funding_fade_bullish_ema_confirm"
                metadata["funding_persistence"] = "negative"
                metadata["ema_cross"] = "bullish"

        # Check for persistent positive funding (short signal)
        persistent_positive_funding = all(f > self.funding_threshold for f in self.funding_history)
        if persistent_positive_funding:
            # Price confirmation: current price is below EMA_short and EMA_short is below EMA_long
            # indicating a potential dip or underlying bearishness despite positive funding
            if ctx.bar.close < ema_short and ema_short < ema_long:
                signal_direction = "short"
                confidence = min(0.7 + (abs(current_funding) / (self.funding_threshold * 2)), 0.9) # Scale confidence
                reason = "persistent_positive_funding_fade_bearish_ema_confirm"
                metadata["funding_persistence"] = "positive"
                metadata["ema_cross"] = "bearish"

        # If we have an open position, check if we should exit or flip
        if ctx.has_position():
            if signal_direction is None:
                # No new signal, so hold current position
                return None
            elif ctx.position_direction() == signal_direction:
                # New signal is in the same direction, hold
                return None
            else:
                # New signal is in the opposite direction, flip position
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(signal_direction, confidence=confidence, metadata={
                    "reason": f"flip_position_from_{ctx.position_direction()}_to_{signal_direction}_{reason}",
                    **metadata
                })
        elif signal_direction:
            # No open position, new signal available
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(signal_direction, confidence=confidence, metadata={
                "reason": reason,
                **metadata
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt strategy based on performance
        pass