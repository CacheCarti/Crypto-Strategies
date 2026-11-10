from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolUsSessionImpulse(Strategy):
    METADATA = {
        "name": "SOL US Session Impulse",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold through next session handoff
        "warmup_bars": 50,
        "required_features": [], # No external features used in this version
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13 # UTC 13:00 (9 AM EST)
        self.us_session_end_hour = 21   # UTC 21:00 (5 PM EST)
        self.cooldown_bars = 5          # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        # Parameters for impulse detection
        self.impulse_period = 4 # Number of bars to check for impulse within session
        self.min_impulse_change_pct = 0.005 # 0.5% move for impulse detection

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # Cooldown logic
        if current_bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit existing positions before US session end, or if impulse structure fails
        if ctx.has_position():
            if current_hour >= self.us_session_end_hour:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "exit_hour": current_hour})

            # Check for structure stop: if the price moves against the impulse significantly
            position = ctx.position
            entry_price = position["entry_price"]
            direction = position["direction"]
            current_price = ctx.bar.close

            # Simple structure stop: if price reverses by more than 0.25% against the entry
            if direction == "long" and (entry_price - current_price) / entry_price > 0.0025:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "structure_stop_long", "entry_price": entry_price, "current_price": current_price})
            elif direction == "short" and (current_price - entry_price) / entry_price > 0.0025:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "structure_stop_short", "entry_price": entry_price, "current_price": current_price})

            # Hold position if within session and no structure stop
            return ctx.signal("hold")

        # Entry logic: Only consider entries during the US session (13:00 - 20:00 UTC)
        # We need enough bars *before* the current bar to detect an impulse
        if not (self.us_session_start_hour <= current_hour < self.us_session_end_hour - self.impulse_period):
            return None

        # Ensure we have enough data for impulse detection
        if len(closes) < self.impulse_period + 1:
            return None

        # Check for impulse within the last self.impulse_period bars
        # This assumes the impulse happens *within* the session, leading to a later entry
        impulse_closes = closes[-(self.impulse_period + 1):]
        start_price = impulse_closes[0]
        end_price = impulse_closes[-1]
        price_change_pct = (end_price - start_price) / start_price

        signal_direction = None
        reason = None
        confidence = 0.0

        # Long impulse
        if price_change_pct > self.min_impulse_change_pct:
            signal_direction = "long"
            reason = "us_session_bullish_impulse"
            confidence = min(0.5 + price_change_pct * 5, 0.9) # Scale confidence with impulse strength
        # Short impulse
        elif price_change_pct < -self.min_impulse_change_pct:
            signal_direction = "short"
            reason = "us_session_bearish_impulse"
            confidence = min(0.5 + abs(price_change_pct) * 5, 0.9) # Scale confidence with impulse strength

        if signal_direction:
            self.last_trade_bar = current_bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "impulse_start_price": start_price,
                    "impulse_end_price": end_price,
                    "price_change_pct": price_change_pct,
                    "entry_hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt sizing.
        # For this strategy, we just ensure cooldown is handled by on_bar.
        pass