from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingFlipUnwind(Strategy):
    METADATA = {
        "name": "Funding Rate Flip Unwind",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Aim for a 4-hour hold
        "warmup_bars": 50, # Need funding rate history and price action
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 8 # Look back 8 bars for stable funding
        self.price_confirm_period = 3 # Look for price confirmation within 3 bars
        self.min_funding_stability = 0.00005 # Minimum funding rate magnitude for stability
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_signal_bar = -self.cooldown_bars

        # For tracking funding rate flips
        self.last_funding_sign = 0 # 1 for positive, -1 for negative, 0 for neutral/first bar
        self.flip_bar_index = -1
        self.flip_direction = None # "long" or "short"
        self.flip_price = 0.0

        # EMA for price confirmation
        self.ema_period = 20

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        current_funding = ctx.features.get("funding_rate_solusdt", 0.0)
        funding_history = [ctx.features.get(f"funding_rate_solusdt", 0.0) for _ in range(self.funding_period)]
        
        # Check for stable funding before a potential flip
        stable_positive = all(f > self.min_funding_stability for f in funding_history[:-1])
        stable_negative = all(f < -self.min_funding_stability for f in funding_history[:-1])

        # Detect a flip
        current_sign = 0
        if current_funding > self.min_funding_stability:
            current_sign = 1
        elif current_funding < -self.min_funding_stability:
            current_sign = -1

        flip_detected = False
        if self.last_funding_sign == 1 and current_sign == -1 and stable_positive:
            # Positive to negative flip (unwind longs -> short)
            self.flip_direction = "short"
            self.flip_bar_index = ctx.bar_index
            self.flip_price = ctx.bar.close
            flip_detected = True
        elif self.last_funding_sign == -1 and current_sign == 1 and stable_negative:
            # Negative to positive flip (unwind shorts -> long)
            self.flip_direction = "long"
            self.flip_bar_index = ctx.bar_index
            self.flip_price = ctx.bar.close
            flip_detected = True
        
        self.last_funding_sign = current_sign

        # If a flip was detected recently, look for price confirmation
        if self.flip_direction and ctx.bar_index <= self.flip_bar_index + self.price_confirm_period:
            ema = self._ema(closes, self.ema_period)
            if ema is None:
                return None

            if ctx.has_position():
                # If we have a position, but the funding flip direction has changed, consider exiting
                if (ctx.position_direction() == "long" and current_sign == -1) or \
                   (ctx.position_direction() == "short" and current_sign == 1):
                    self.last_signal_bar = ctx.bar_index # Reset cooldown
                    self.flip_direction = None # Reset flip state
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "funding_flip_failed_or_reversed", "current_funding": current_funding})
                return None # Hold position if no new signal

            # Price confirmation
            if self.flip_direction == "long":
                # For a long signal, price should be above EMA or crossing it up
                if ctx.bar.close > ema and closes[-2] <= ema: # Price crossed above EMA
                    self.last_signal_bar = ctx.bar_index
                    signal_metadata = {
                        "reason": "funding_flip_positive_price_confirm",
                        "flip_funding_rate": current_funding,
                        "flip_bar_index": self.flip_bar_index,
                        "confirmation_price": ctx.bar.close,
                        "ema": ema,
                        "current_funding_sign": current_sign,
                        "previous_funding_sign": self.last_funding_sign # This will be the current_sign from the previous bar
                    }
                    self.flip_direction = None # Reset flip state
                    return ctx.signal("long", confidence=0.8, metadata=signal_metadata)
                elif ctx.bar.close > ema and (ctx.bar_index - self.flip_bar_index) == 1: # Price already above EMA at flip + 1 bar
                    self.last_signal_bar = ctx.bar_index
                    signal_metadata = {
                        "reason": "funding_flip_positive_price_confirm_immediate",
                        "flip_funding_rate": current_funding,
                        "flip_bar_index": self.flip_bar_index,
                        "confirmation_price": ctx.bar.close,
                        "ema": ema,
                        "current_funding_sign": current_sign,
                        "previous_funding_sign": self.last_funding_sign
                    }
                    self.flip_direction = None # Reset flip state
                    return ctx.signal("long", confidence=0.7, metadata=signal_metadata)

            elif self.flip_direction == "short":
                # For a short signal, price should be below EMA or crossing it down
                if ctx.bar.close < ema and closes[-2] >= ema: # Price crossed below EMA
                    self.last_signal_bar = ctx.bar_index
                    signal_metadata = {
                        "reason": "funding_flip_negative_price_confirm",
                        "flip_funding_rate": current_funding,
                        "flip_bar_index": self.flip_bar_index,
                        "confirmation_price": ctx.bar.close,
                        "ema": ema,
                        "current_funding_sign": current_sign,
                        "previous_funding_sign": self.last_funding_sign
                    }
                    self.flip_direction = None # Reset flip state
                    return ctx.signal("short", confidence=0.8, metadata=signal_metadata)
                elif ctx.bar.close < ema and (ctx.bar_index - self.flip_bar_index) == 1: # Price already below EMA at flip + 1 bar
                    self.last_signal_bar = ctx.bar_index
                    signal_metadata = {
                        "reason": "funding_flip_negative_price_confirm_immediate",
                        "flip_funding_rate": current_funding,
                        "flip_bar_index": self.flip_bar_index,
                        "confirmation_price": ctx.bar.close,
                        "ema": ema,
                        "current_funding_sign": current_sign,
                        "previous_funding_sign": self.last_funding_sign
                    }
                    self.flip_direction = None # Reset flip state
                    return ctx.signal("short", confidence=0.7, metadata=signal_metadata)

        # If price confirmation period passed without a signal, reset flip state
        if self.flip_direction and ctx.bar_index > self.flip_bar_index + self.price_confirm_period:
            self.flip_direction = None
            self.flip_bar_index = -1
            self.flip_price = 0.0

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after any position closes
        self.last_signal_bar = ctx.bar_index
        # You could add adaptive logic here, e.g., if return_bps is very good/bad, adjust confidence
        pass