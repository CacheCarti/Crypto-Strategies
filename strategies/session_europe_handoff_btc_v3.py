from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionEuropeHandoffBTC(Strategy):
    METADATA = {
        "name": "SessionEuropeHandoffBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for 1-2 hour hold
        "warmup_bars": 30,
        "required_features": [], # No specific features required for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7
        self.entry_hour_end = 12   # UTC 12 (exclusive, so up to 11:59)
        self.exit_hour = 12        # UTC 12 (close positions by this hour)
        self.max_hold_bars = 4     # Max 4 bars (4 hours)
        self.min_session_bars = 3  # Need at least 3 bars to establish session direction
        self.cooldown_bars = 6     # Cooldown for 6 bars (6 hours) after a trade
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        # Parameters for impulse detection
        self.impulse_period = 3
        self.impulse_threshold = 0.0005 # 0.05% price change over impulse_period bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for cooldown
        if ctx.bar_index <= self.last_trade_bar_index + self.cooldown_bars:
            return None

        # --- Position Management (Exit Logic) ---
        if ctx.has_position():
            if ctx.bar_index - self.last_trade_bar_index >= self.max_hold_bars:
                # Close due to max hold time
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "max_hold_time_reached", "bars_held": ctx.bar_index - self.last_trade_bar_index})
            
            if current_hour == self.exit_hour and ctx.position_direction() is not None:
                # Close at session handoff hour
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_handoff_exit", "exit_hour": self.exit_hour})
            
            return ctx.signal("hold") # Hold existing position if no exit condition met

        # --- Entry Logic ---
        # Only consider entries during the specified European morning session
        if not (self.entry_hour_start <= current_hour < self.entry_hour_end):
            return None

        # Need enough bars to evaluate the session's impulse
        if len(closes) < self.min_session_bars + self.impulse_period:
            return None

        # Calculate session impulse
        # We need the closes from the beginning of the current session up to now
        # For simplicity, we'll use the last N bars leading up to the current bar
        # that fall within the session hours, assuming a continuous session.
        # This is a simplification as bars might not all be from *this* session if it just started.
        # A more robust approach would track session start bar index.
        
        # We need to detect a *transition* in impulse, not just a continuous impulse.
        # Let's look at the price change over the last `impulse_period` bars.
        price_change = closes[-1] - closes[-self.impulse_period - 1]
        relative_price_change = price_change / closes[-self.impulse_period - 1]

        # Check for a significant directional impulse
        if relative_price_change > self.impulse_threshold:
            # Long impulse detected
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                metadata={
                    "reason": "europe_session_long_impulse",
                    "current_hour": current_hour,
                    "relative_price_change": relative_price_change,
                    "impulse_period": self.impulse_period
                }
            )
        elif relative_price_change < -self.impulse_threshold:
            # Short impulse detected
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                metadata={
                    "reason": "europe_session_short_impulse",
                    "current_hour": current_hour,
                    "relative_price_change": relative_price_change,
                    "impulse_period": self.impulse_period
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar index to current bar index after any position closes
        # This ensures the cooldown applies even if the position was closed by SL/TP
        self.last_trade_bar_index = ctx.bar_index
        # You could use this for adaptive sizing or logging if needed
        # print(f"Position closed: {position}")