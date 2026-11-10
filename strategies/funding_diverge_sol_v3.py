from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolFundingDivergence(Strategy):
    METADATA = {
        "name": "SOL Funding Divergence",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for 4-hour hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24  # Lookback for funding rate trend (24 hours)
        self.price_period = 24    # Lookback for price trend (24 hours)
        self.cooldown_bars = 5    # Cooldown period after a trade
        self.last_signal_bar = -self.cooldown_bars

        # Store previous funding rate for transition detection
        self.prev_funding_rate: Optional[float] = None
        self.prev_price_change: Optional[float] = None

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.price_period + 1)
        funding_rates = [ctx.features.get("funding_rate_solusdt", 0.0)]
        # For simplicity, we just use the current funding rate.
        # A more complex strategy could average past funding rates.

        if len(closes) < self.price_period + 1 or ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_signal_bar + self.cooldown_bars:
            return None

        current_funding_rate = funding_rates[-1]
        price_change = (closes[-1] - closes[-self.price_period]) / closes[-self.price_period]

        signal = None
        confidence = 0.0
        reason = ""
        metadata = {}

        # Long signal: Falling price with negative funding (potential short squeeze)
        # Price has dropped significantly AND funding is negative (shorts paying longs)
        # And funding is becoming more negative OR staying very negative
        if (price_change < -0.015 and current_funding_rate < -0.0001): # Price dropped 1.5%, funding is negative
            # Check for funding becoming more negative or staying very negative
            if self.prev_funding_rate is not None and current_funding_rate < self.prev_funding_rate:
                signal = "long"
                confidence = 0.7
                reason = "sol_short_squeeze_divergence"
                metadata = {
                    "price_change_pct": price_change * 100,
                    "current_funding_rate": current_funding_rate,
                    "prev_funding_rate": self.prev_funding_rate
                }
            elif self.prev_funding_rate is None and current_funding_rate < -0.0002: # Initial very strong signal
                signal = "long"
                confidence = 0.6
                reason = "sol_short_squeeze_divergence_initial"
                metadata = {
                    "price_change_pct": price_change * 100,
                    "current_funding_rate": current_funding_rate
                }

        # Short signal: Rising price with positive funding (potential long liquidation)
        # Price has risen significantly AND funding is positive (longs paying shorts)
        # And funding is becoming more positive OR staying very positive
        elif (price_change > 0.015 and current_funding_rate > 0.0001): # Price rose 1.5%, funding is positive
            # Check for funding becoming more positive or staying very positive
            if self.prev_funding_rate is not None and current_funding_rate > self.prev_funding_rate:
                signal = "short"
                confidence = 0.7
                reason = "sol_long_liquidation_divergence"
                metadata = {
                    "price_change_pct": price_change * 100,
                    "current_funding_rate": current_funding_rate,
                    "prev_funding_rate": self.prev_funding_rate
                }
            elif self.prev_funding_rate is None and current_funding_rate > 0.0002: # Initial very strong signal
                signal = "short"
                confidence = 0.6
                reason = "sol_long_liquidation_divergence_initial"
                metadata = {
                    "price_change_pct": price_change * 100,
                    "current_funding_rate": current_funding_rate
                }

        # Update previous values for next bar's transition detection
        self.prev_funding_rate = current_funding_rate
        self.prev_price_change = price_change

        # If a signal is generated, check for existing position
        if signal:
            if ctx.has_position():
                if ctx.position_direction() == signal:
                    # Already in position, hold
                    return ctx.signal("hold")
                else:
                    # Flip position
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(signal, confidence=confidence, metadata=metadata)
            else:
                # No position, enter
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(signal, confidence=confidence, metadata=metadata)
        
        # If no signal, and holding a position, just hold
        if ctx.has_position():
            return ctx.signal("hold")

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_signal_bar to enforce cooldown after any position close
        self.last_signal_bar = ctx.bar_index
        # You could also use this to adapt parameters, e.g., if many losses, reduce confidence threshold.
        # For this strategy, we keep it simple.
        pass