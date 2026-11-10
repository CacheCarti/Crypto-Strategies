from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingFlipConfirmation(Strategy):
    METADATA = {
        "name": "Funding Flip Confirmation",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Aim for 4-hour holds
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.funding_threshold = 0.0001 # 0.01% for 8h period
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

        # State to track funding rate changes
        self.prev_funding_rate: Optional[float] = None
        self.funding_flip_bar_index: Optional[int] = None
        self.funding_flip_direction: Optional[str] = None # "long" or "short"

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + 1)
        if len(closes) < self.slow_ema_period + 1:
            return None

        current_funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)

        # Check for funding rate flip
        if self.prev_funding_rate is not None:
            if self.prev_funding_rate < -self.funding_threshold and current_funding_rate >= self.funding_threshold:
                # Strong flip from negative to positive
                self.funding_flip_bar_index = ctx.bar_index
                self.funding_flip_direction = "long"
            elif self.prev_funding_rate > self.funding_threshold and current_funding_rate <= -self.funding_threshold:
                # Strong flip from positive to negative
                self.funding_flip_bar_index = ctx.bar_index
                self.funding_flip_direction = "short"
        
        self.prev_funding_rate = current_funding_rate

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Price confirmation logic
        if self.funding_flip_bar_index is not None and ctx.bar_index <= self.funding_flip_bar_index + 3: # Look for confirmation within 3 bars
            if self.funding_flip_direction == "long":
                # Price confirmation for long: fast EMA crosses above slow EMA
                if fast_ema > slow_ema and ctx.closes(2)[-2] < ctx.opens(2)[-2] and ctx.bar.close > ctx.bar.open: # Previous bar red, current bar green
                    if not ctx.has_position():
                        self.last_signal_bar = ctx.bar_index
                        self.funding_flip_bar_index = None # Reset flip
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "funding_flip_long_ema_cross_confirmation",
                                "current_funding": current_funding_rate,
                                "prev_funding": self.prev_funding_rate,
                                "fast_ema": fast_ema,
                                "slow_ema": slow_ema,
                                "close": ctx.bar.close,
                            }
                        )
            elif self.funding_flip_direction == "short":
                # Price confirmation for short: fast EMA crosses below slow EMA
                if fast_ema < slow_ema and ctx.closes(2)[-2] > ctx.opens(2)[-2] and ctx.bar.close < ctx.bar.open: # Previous bar green, current bar red
                    if not ctx.has_position():
                        self.last_signal_bar = ctx.bar_index
                        self.funding_flip_bar_index = None # Reset flip
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            metadata={
                                "reason": "funding_flip_short_ema_cross_confirmation",
                                "current_funding": current_funding_rate,
                                "prev_funding": self.prev_funding_rate,
                                "fast_ema": fast_ema,
                                "slow_ema": slow_ema,
                                "close": ctx.bar.close,
                            }
                        )
            
            # If we pass the confirmation window without a signal, invalidate the flip
            if ctx.bar_index == self.funding_flip_bar_index + 3:
                self.funding_flip_bar_index = None
                self.funding_flip_direction = None

        # Exit logic: EMA crossover against position
        if ctx.has_position():
            if ctx.position_direction() == "long" and fast_ema < slow_ema:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "ema_cross_exit_long",
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "close": ctx.bar.close,
                    }
                )
            elif ctx.position_direction() == "short" and fast_ema > slow_ema:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "ema_cross_exit_short",
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "close": ctx.bar.close,
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis
        pass