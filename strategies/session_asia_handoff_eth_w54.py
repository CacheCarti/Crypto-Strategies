from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionImpulse(Strategy):
    METADATA = {
        "name": "Asia Session Impulse",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for ~1 hour hold, but allow up to 8 hours
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_session_high = -1.0
        self.asia_session_low = -1.0
        self.session_start_bar_index = -1
        self.last_signal_bar_index = -1
        self.cooldown_bars = 5 # Wait 5 bars after a trade to re-enter
        self.traded_today = False # To ensure only one trade per session

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset session data at the start of the UTC day (00:00 UTC)
        if current_hour == 0 and self.session_start_bar_index != ctx.bar_index:
            self.asia_session_high = ctx.bar.high
            self.asia_session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
            self.traded_today = False
        elif current_hour >= 0 and current_hour < 8: # Asia session (UTC 0-8)
            # Update session high/low
            if ctx.bar.high > self.asia_session_high:
                self.asia_session_high = ctx.bar.high
            if ctx.bar.low < self.asia_session_low:
                self.asia_session_low = ctx.bar.low

        # --- Exit Logic ---
        if ctx.has_position():
            # Mandatory exit before UTC 8:00
            if current_hour >= 8:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_end_exit",
                    "current_price": ctx.bar.close,
                    "exit_hour": current_hour
                })
            return None # Hold position until session end or stop/take profit

        # --- Entry Logic ---
        # Cooldown period after a previous signal
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # Only enter if we haven't traded yet today and it's during the Asia session
        if not self.traded_today and current_hour >= 0 and current_hour < 8:
            # Wait for a few bars into the session to establish range
            if ctx.bar_index - self.session_start_bar_index < 3:
                return None

            # Look for a breakout from the Asia session range
            # Long entry: price breaks above session high with conviction
            if ctx.bar.close > self.asia_session_high and ctx.bar.open < self.asia_session_high:
                self.last_signal_bar_index = ctx.bar_index
                self.traded_today = True
                return ctx.signal("long", confidence=0.75, metadata={
                    "reason": "asia_session_high_breakout",
                    "breakout_price": ctx.bar.close,
                    "session_high": self.asia_session_high,
                    "current_hour": current_hour
                })

            # Short entry: price breaks below session low with conviction
            if ctx.bar.close < self.asia_session_low and ctx.bar.open > self.asia_session_low:
                self.last_signal_bar_index = ctx.bar_index
                self.traded_today = True
                return ctx.signal("short", confidence=0.75, metadata={
                    "reason": "asia_session_low_breakout",
                    "breakout_price": ctx.bar.close,
                    "session_low": self.asia_session_low,
                    "current_hour": current_hour
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset traded_today flag if the position closed by stop/profit before the session end
        if position.get("bars_held", 0) > 0 and ctx.bar.timestamp.hour < 8:
            self.traded_today = False # Allow re-entry if early exit, otherwise it's reset at 00:00
        pass