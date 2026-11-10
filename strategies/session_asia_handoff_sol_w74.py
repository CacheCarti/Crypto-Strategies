from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionImpulse(Strategy):
    METADATA = {
        "name": "Asia Session Impulse",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Max hold until end of session
        "warmup_bars": 50,
        "required_features": [], # No specific features required for this impulse logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_length = 8 # hours (UTC 0-8)
        self.lookback_period = 8 # bars for session impulse
        self.ema_fast_period = 10
        self.ema_slow_period = 30
        self.cooldown_bars = 4 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow first trade immediately
        self.traded_today_utc0_8 = False # Flag to trade only once per Asia session
        self.last_bar_hour = -1 # To detect new hour for session logic

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1) # Need one more for EMA calculations
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset traded_today_utc0_8 flag at the start of a new UTC day (after 8 AM UTC)
        if current_hour >= 8 and self.last_bar_hour < 8:
            self.traded_today_utc0_8 = False

        self.last_bar_hour = current_hour

        # Do not trade if in cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # --- Session Logic ---
        # Asia session is UTC 0-8. We want to analyze the impulse within this session.
        # Entry logic will be at the end of the Asia session or beginning of next.
        is_asia_session = (current_hour >= 0 and current_hour < 8)

        # Calculate EMAs
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if ema_fast is None or ema_slow is None:
            return None

        # If a position is open, check if it's time to close
        if ctx.has_position():
            # If current time is after Asia session (e.g., 8 AM UTC or later)
            # and the position was opened based on Asia impulse,
            # or if EMAs cross against the position direction, exit.
            position_direction = ctx.position_direction()
            
            # Exit if we are past the intended hold time (e.g., 4 hours after session end)
            if current_hour >= 8 and position_direction == "long":
                # If we entered based on Asia session, and it's now after 8 AM UTC, flat the position.
                # This ensures we don't hold positions too long based on this specific thesis.
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "asia_session_exit_time",
                    "current_hour": current_hour,
                    "entry_hour": ctx.position["metadata"].get("entry_hour", -1)
                })
            
            if current_hour >= 8 and position_direction == "short":
                 return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "asia_session_exit_time",
                    "current_hour": current_hour,
                    "entry_hour": ctx.position["metadata"].get("entry_hour", -1)
                })


            # Adaptive exit based on EMA cross against position
            if position_direction == "long" and ema_fast < ema_slow: # Bullish cross reverses
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "ema_cross_against_long",
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "current_price": closes[-1]
                })
            elif position_direction == "short" and ema_fast > ema_slow: # Bearish cross reverses
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "ema_cross_against_short",
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "current_price": closes[-1]
                })
            return None # Hold position

        # --- Entry Logic ---
        # We want to enter at the end of the Asia session (e.g., 7 AM UTC)
        # or shortly after, based on the impulse developed during the Asia session.
        # Only enter if not already traded in this session block (UTC 0-8)
        if not self.traded_today_utc0_8 and current_hour == 7: # Trigger at 7 AM UTC (last hour of Asia session)
            # Look at the price action over the Asia session (last `lookback_period` bars)
            session_closes = ctx.closes(self.lookback_period)
            session_opens = ctx.opens(self.lookback_period)
            if len(session_closes) < self.lookback_period or len(session_opens) < self.lookback_period:
                return None

            # Calculate the total change during the session
            session_change = session_closes[-1] - session_opens[0]
            
            # Check for strong directional impulse
            # We want to see a clear trend over the session, confirmed by EMA setup
            
            # Long condition: Price increased significantly during Asia session, and EMAs are bullish
            if session_change > 0 and ema_fast > ema_slow and closes[-1] > ema_fast:
                self.traded_today_utc0_8 = True
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "asia_session_bullish_impulse",
                        "session_change_pct": (session_change / session_opens[0]) * 100,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "current_hour": current_hour,
                        "entry_hour": current_hour
                    }
                )
            # Short condition: Price decreased significantly during Asia session, and EMAs are bearish
            elif session_change < 0 and ema_fast < ema_slow and closes[-1] < ema_fast:
                self.traded_today_utc0_8 = True
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "asia_session_bearish_impulse",
                        "session_change_pct": (session_change / session_opens[0]) * 100,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "current_hour": current_hour,
                        "entry_hour": current_hour
                    }
                )
        
        return None