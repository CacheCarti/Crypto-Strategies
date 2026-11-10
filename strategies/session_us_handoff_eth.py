from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionHandoffETH(Strategy):
    METADATA = {
        "name": "US Session Handoff ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, intended hold through next session handoff
        "warmup_bars": 50,
        "required_features": [], # No specific features required beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 13 # UTC 13:00 (US market open)
        self.entry_hour_end = 16   # UTC 16:00 (mid-US session for impulse detection)
        self.exit_hour = 21        # UTC 21:00 (end of US session, before Asia)
        self.min_bars_since_trade = 12 # Cooldown of 12 hours (12 bars * 1h/bar)

        self.last_trade_bar_index = -self.min_bars_since_trade * 2 # Initialize far in past

        self.session_impulse_period = 4 # Bars to measure impulse (13:00 to 16:00 inclusive is 4 bars)
        self.entry_confirm_period = 1 # Look for confirmation in the next bar

        # Initialize variables for session tracking
        self.session_open_price: Optional[float] = None
        self.session_direction: Optional[str] = None
        self.session_high: Optional[float] = None
        self.session_low: Optional[float] = None
        self.session_start_bar_index: Optional[int] = None

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for cooldown
        if ctx.bar_index - self.last_trade_bar_index < self.min_bars_since_trade:
            if ctx.has_position():
                # If in a position, still check for exit conditions
                if current_hour >= self.exit_hour and ctx.position_direction() is not None:
                    return ctx.signal("flat", confidence=0.9, metadata={"reason": "session_close_time_reached", "current_hour": current_hour})
                return None # In cooldown, but not yet exit hour
            else:
                return None # In cooldown, no position

        # --- Session Impulse Detection ---
        # Reset session tracking at the start of the US session (13:00 UTC)
        if current_hour == self.entry_hour_start and (self.session_start_bar_index is None or ctx.bar_index > self.session_start_bar_index):
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_direction = None
            self.session_start_bar_index = ctx.bar_index
        elif self.session_start_bar_index is not None and ctx.bar_index > self.session_start_bar_index:
            # Update session high/low during the session
            if ctx.bar.high > self.session_high:
                self.session_high = ctx.bar.high
            if ctx.bar.low < self.session_low:
                self.session_low = ctx.bar.low

        # Determine session impulse between entry_hour_start and entry_hour_end
        if self.session_open_price is not None and \
           current_hour == self.entry_hour_end and \
           self.session_direction is None and \
           ctx.bar_index >= self.session_start_bar_index + self.session_impulse_period - 1: # After enough bars for impulse
            
            # Use the close of the last bar in the impulse period to determine direction
            impulse_close = ctx.bar.close
            
            # Simple directional impulse from session open
            if impulse_close > self.session_open_price:
                self.session_direction = "long"
            elif impulse_close < self.session_open_price:
                self.session_direction = "short"
            else:
                self.session_direction = "neutral"

        # --- Entry Logic (after impulse detection, before exit hour) ---
        if not ctx.has_position() and self.session_direction is not None and self.session_direction != "neutral":
            # Entry condition: one bar after impulse detection, current price confirms impulse
            # This allows for "handoff" confirmation
            if current_hour == self.entry_hour_end + self.entry_confirm_period:
                sma_20 = self._sma(closes, 20)
                if sma_20 is None:
                    return None

                entry_price = ctx.bar.close # Use current close for entry
                
                # Long entry: Session impulse was up, and current price is above SMA20
                if self.session_direction == "long" and entry_price > sma_20:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "us_session_long_handoff_confirmed",
                            "session_open_price": self.session_open_price,
                            "entry_price": entry_price,
                            "sma20": sma_20,
                            "current_hour": current_hour
                        }
                    )
                # Short entry: Session impulse was down, and current price is below SMA20
                elif self.session_direction == "short" and entry_price < sma_20:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "us_session_short_handoff_confirmed",
                            "session_open_price": self.session_open_price,
                            "entry_price": entry_price,
                            "sma20": sma_20,
                            "current_hour": current_hour
                        }
                    )

        # --- Exit Logic (Time-based or Stop-Loss/Take-Profit managed by engine) ---
        if ctx.has_position():
            # Mandatory exit by end of US session (UTC 21:00)
            if current_hour >= self.exit_hour and ctx.position_direction() is not None:
                return ctx.signal("flat", confidence=0.9, metadata={"reason": "session_close_time_reached", "current_hour": current_hour})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session direction on close to allow for a new session detection
        # This ensures we don't carry over session direction if a position closes early
        self.session_direction = None
        self.session_open_price = None
        self.session_high = None
        self.session_low = None
        self.session_start_bar_index = None

        # Update last_trade_bar_index to enforce cooldown
        self.last_trade_bar_index = ctx.bar_index