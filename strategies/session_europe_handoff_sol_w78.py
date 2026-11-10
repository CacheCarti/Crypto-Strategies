from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanSessionImpulse(Strategy):
    METADATA = {
        "name": "European Session Impulse",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold for one session, 1 hour bar
        "warmup_bars": 24, # Need enough bars for 24-hour lookback for session range
        "required_features": [], # No specific features used beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7 AM
        self.entry_hour_end = 9    # UTC 9 AM (inclusive, so bar starting at 9 AM is still valid)
        self.exit_hour = 12        # UTC 12 PM (bar starting at 12 PM will trigger exit)
        self.session_lookback_bars = 24 # 24 bars for 24-hour range for session impulse
        self.min_impulse_percentage = 0.5 # Minimum 0.5% move to consider it an impulse
        self.cooldown_bars = 6 # Wait 6 bars (6 hours) after a trade before new entry
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.session_lookback_bars + 1)
        opens = ctx.opens(self.session_lookback_bars + 1)
        highs = ctx.highs(self.session_lookback_bars + 1)
        lows = ctx.lows(self.session_lookback_bars + 1)

        if len(closes) < self.session_lookback_bars + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Always check for position exit first
        if ctx.has_position():
            # Exit at the end of the session or if it's past the exit hour
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_time_exit",
                    "current_hour": current_hour,
                    "exit_hour": self.exit_hour,
                    "bar_index": ctx.bar_index
                })
            # Hold if not time to exit
            return ctx.signal("hold")

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar_index <= self.cooldown_bars:
            return None

        # Entry logic within the specified European morning hours
        if self.entry_hour_start <= current_hour <= self.entry_hour_end:
            # Calculate the range and direction of the previous session (e.g., last 24 bars)
            session_high = max(highs[:-1]) # High of previous session
            session_low = min(lows[:-1])   # Low of previous session
            session_open = opens[0]        # Open of the previous session
            session_close = closes[-2]     # Close of the previous session

            # Calculate impulse based on the previous 24-hour session's movement
            if session_open == 0: # Avoid division by zero
                return None

            impulse_pct = (session_close - session_open) / session_open * 100

            # Calculate current bar's movement relative to the session range
            current_bar_move_from_session_open_pct = (ctx.bar.close - session_open) / session_open * 100

            # Long signal: Strong upward impulse in previous session, and current bar confirms follow-through
            if impulse_pct > self.min_impulse_percentage and current_bar_move_from_session_open_pct > 0:
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("long", confidence=0.75, metadata={
                    "reason": "eu_session_bullish_impulse_follow_through",
                    "entry_hour": current_hour,
                    "session_open": session_open,
                    "session_close": session_close,
                    "impulse_pct": impulse_pct,
                    "current_bar_move_from_session_open_pct": current_bar_move_from_session_open_pct,
                    "bar_index": ctx.bar_index
                })

            # Short signal: Strong downward impulse in previous session, and current bar confirms follow-through
            elif impulse_pct < -self.min_impulse_percentage and current_bar_move_from_session_open_pct < 0:
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("short", confidence=0.75, metadata={
                    "reason": "eu_session_bearish_impulse_follow_through",
                    "entry_hour": current_hour,
                    "session_open": session_open,
                    "session_close": session_close,
                    "impulse_pct": impulse_pct,
                    "current_bar_move_from_session_open_pct": current_bar_move_from_session_open_pct,
                    "bar_index": ctx.bar_index
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally, log position close details or adapt future trading
        pass