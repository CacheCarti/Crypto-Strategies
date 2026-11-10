from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightSessionBreakout(Strategy):
    METADATA = {
        "name": "Overnight Session Breakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Intended hold through next session handoff (2 hours)
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_period = 6  # UTC 0-6
        self.breakout_period = 12  # Lookback period for high/low in overnight
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # Store session open/close prices and range for the overnight session
        self.overnight_session_data = {
            "start_bar_index": -1,
            "open_price": None,
            "high_price": None,
            "low_price": None,
            "close_price": None,
            "session_range": None
        }
        self.session_traded_today = False # To ensure only one trade per session/day

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        highs = ctx.highs(self.METADATA["warmup_bars"] + 1)
        lows = ctx.lows(self.METADATA["warmup_bars"] + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # --- Session Tracking and Data Collection ---
        # Start of overnight session (UTC 0)
        if current_hour == 0 and self.overnight_session_data["start_bar_index"] != ctx.bar_index:
            # Reset for a new session
            self.overnight_session_data = {
                "start_bar_index": ctx.bar_index,
                "open_price": ctx.bar.open,
                "high_price": ctx.bar.high,
                "low_price": ctx.bar.low,
                "close_price": ctx.bar.close,
                "session_range": None
            }
            self.session_traded_today = False
        # During overnight session (UTC 0-5)
        elif 0 <= current_hour < self.overnight_period:
            if self.overnight_session_data["start_bar_index"] != -1:
                self.overnight_session_data["high_price"] = max(self.overnight_session_data["high_price"], ctx.bar.high)
                self.overnight_session_data["low_price"] = min(self.overnight_session_data["low_price"], ctx.bar.low)
                self.overnight_session_data["close_price"] = ctx.bar.close
        # End of overnight session (UTC 6) - calculate session range
        elif current_hour == self.overnight_period and self.overnight_session_data["session_range"] is None and self.overnight_session_data["start_bar_index"] != -1:
            self.overnight_session_data["high_price"] = max(self.overnight_session_data["high_price"], ctx.bar.high)
            self.overnight_session_data["low_price"] = min(self.overnight_session_data["low_price"], ctx.bar.low)
            self.overnight_session_data["close_price"] = ctx.bar.close
            # Ensure high/low are valid before calculating range
            if self.overnight_session_data["high_price"] is not None and self.overnight_session_data["low_price"] is not None:
                self.overnight_session_data["session_range"] = self.overnight_session_data["high_price"] - self.overnight_session_data["low_price"]
            else:
                self.overnight_session_data["session_range"] = 0.0 # Default if no range was established
            
        # --- Trading Logic ---
        # Exit existing position logic
        if ctx.has_position():
            # Exit if past the next session handoff (e.g., UTC 12-14, before US session fully kicks in)
            # We'll use UTC 14 as a general exit boundary for multi-hour holds
            if current_hour >= 14:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_boundary_exit", "current_hour": current_hour})
            return None # Hold position if not time to exit

        # Entry logic (only if no position and cooldown passed)
        if not ctx.has_position() and ctx.bar_index >= self.last_trade_bar + self.cooldown_bars and not self.session_traded_today:
            # Entry condition: After overnight session (from UTC 7 up to UTC 13)
            if self.overnight_session_data["session_range"] is not None and self.overnight_session_data["session_range"] > 0:
                overnight_high = self.overnight_session_data["high_price"]
                overnight_low = self.overnight_session_data["low_price"]

                # We want to trade the breakout of the overnight session high/low
                # and carry it into the next session handoff.
                # Only consider trades during the active "follow-through" window, e.g., 7 AM to 1 PM UTC
                if 7 <= current_hour <= 13:
                    # Long signal: price breaks above overnight high
                    if ctx.bar.close > overnight_high:
                        self.session_traded_today = True
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            metadata={
                                "reason": "overnight_high_breakout",
                                "overnight_high": overnight_high,
                                "current_price": ctx.bar.close,
                                "session_range": self.overnight_session_data["session_range"],
                                "current_hour": current_hour
                            }
                        )
                    # Short signal: price breaks below overnight low
                    elif ctx.bar.close < overnight_low:
                        self.session_traded_today = True
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            metadata={
                                "reason": "overnight_low_breakdown",
                                "overnight_low": overnight_low,
                                "current_price": ctx.bar.close,
                                "session_range": self.overnight_session_data["session_range"],
                                "current_hour": current_hour
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for debugging or adaptive sizing
        pass