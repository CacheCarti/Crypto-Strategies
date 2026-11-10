from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaHandoffBTC(Strategy):
    METADATA = {
        "name": "Asia Handoff BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Approx 1-2 bars
        "warmup_bars": 24, # Need enough bars to calculate session range
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1

        self.asia_session_start_hour = 0 # UTC 00:00
        self.asia_session_end_hour = 8   # UTC 08:00 (exclusive)
        self.entry_window_start_hour = 8 # UTC 08:00
        self.entry_window_end_hour = 9   # UTC 09:00 (exclusive)

        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open = None
        self.session_close = None
        self.session_bars_count = 0
        self.session_direction = "neutral"

    def _reset_session_data(self):
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open = None
        self.session_close = None
        self.session_bars_count = 0
        self.session_direction = "neutral"

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        current_hour = ctx.bar.timestamp.hour
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # --- Session Data Collection ---
        if current_hour >= self.asia_session_start_hour and current_hour < self.asia_session_end_hour:
            # Inside Asia session
            if self.session_open is None:
                self.session_open = ctx.bar.open
                self.session_high = ctx.bar.high
                self.session_low = ctx.bar.low
            else:
                self.session_high = max(self.session_high, ctx.bar.high)
                self.session_low = min(self.session_low, ctx.bar.low)
            self.session_close = ctx.bar.close
            self.session_bars_count += 1
        elif self.session_bars_count > 0 and current_hour == self.asia_session_end_hour:
            # Just finished Asia session, calculate direction
            if self.session_close > self.session_open:
                self.session_direction = "long"
            elif self.session_close < self.session_open:
                self.session_direction = "short"
            else:
                self.session_direction = "neutral"
            # Reset for next session after calculation
            self.session_bars_count = 0 # Mark as processed for this session

        # --- Exit Logic (if position exists) ---
        if ctx.has_position():
            # Close position before the next session handoff if it's still open
            if current_hour >= self.entry_window_end_hour and ctx.position_direction() == self.session_direction:
                # Close if we're past the intended hold time for the session
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_handoff_time_exit",
                    "entry_hour": self.entry_window_start_hour,
                    "exit_hour": current_hour
                })
            # Or if it's a new session starting (UTC 00:00)
            if current_hour == self.asia_session_start_hour and ctx.position_direction() != "flat":
                 return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "new_day_session_reset_exit",
                    "entry_hour": ctx.bar.timestamp.hour,
                    "exit_hour": current_hour
                })

        # --- Entry Logic ---
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None # Cooldown active

        if not ctx.has_position() and self.session_direction != "neutral":
            # Entry window: one hour after Asia session close
            if current_hour >= self.entry_window_start_hour and current_hour < self.entry_window_end_hour:
                session_range = self.session_high - self.session_low
                # Only enter if the session had a decent range
                if session_range > 0 and ctx.bar.close > 0.001 * ctx.bar.open: # Minimal range check

                    # Check for follow-through in the current bar
                    if self.session_direction == "long":
                        # Price should be breaking above session high or showing strength
                        if ctx.bar.close > self.session_high and ctx.bar.close > ctx.bar.open:
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.8,
                                metadata={
                                    "reason": "asia_session_long_handoff_breakout",
                                    "session_open": self.session_open,
                                    "session_close": self.session_close,
                                    "session_high": self.session_high,
                                    "session_low": self.session_low,
                                    "current_close": ctx.bar.close,
                                    "current_open": ctx.bar.open,
                                    "session_direction": self.session_direction
                                }
                            )
                    elif self.session_direction == "short":
                        # Price should be breaking below session low or showing weakness
                        if ctx.bar.close < self.session_low and ctx.bar.close < ctx.bar.open:
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.8,
                                metadata={
                                    "reason": "asia_session_short_handoff_breakdown",
                                    "session_open": self.session_open,
                                    "session_close": self.session_close,
                                    "session_high": self.session_high,
                                    "session_low": self.session_low,
                                    "current_close": ctx.bar.close,
                                    "current_open": ctx.bar.open,
                                    "session_direction": self.session_direction
                                }
                            )
        
        # Reset session data at the start of a new day (UTC 00:00)
        # This ensures that session data is only carried over for one 'cycle'
        if current_hour == self.asia_session_start_hour:
            self._reset_session_data()

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally log or adapt based on position close
        pass