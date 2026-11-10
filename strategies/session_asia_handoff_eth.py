from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthAsiaSessionHandoff(Strategy):
    METADATA = {
        "name": "ETH Asia Session Handoff",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aiming for 1-2 session holds
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this session-based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_period = 8 # Asia session is 8 hours (UTC 0-8)
        self.entry_threshold_pct = 0.002 # 0.2% move to confirm impulse
        self.cooldown_bars = 12 # Wait 12 hours after a trade (12 bars for 1h chart)
        self.last_trade_bar = -self.cooldown_bars
        self.daily_entry_made = False # Flag to ensure only one entry per UTC day
        self.last_bar_day = -1 # To track day changes

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.session_period * 2) # Need enough data for two sessions
        opens = ctx.opens(self.session_period * 2)
        highs = ctx.highs(self.session_period * 2)
        lows = ctx.lows(self.session_period * 2)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset daily entry flag at the start of a new UTC day (00:00 UTC)
        if current_hour == 0 and ctx.bar.timestamp.day != self.last_bar_day:
            self.daily_entry_made = False
            self.last_bar_day = ctx.bar.timestamp.day

        # Check for cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit existing position if it's nearing the end of the trading window
        if ctx.has_position():
            # If position is long, and it's 7 AM UTC, prepare to exit
            # We want to exit before the end of the Asia session (UTC 8)
            # Or if it's held for too long (e.g., 2 sessions)
            if ctx.position["direction"] == "long" and current_hour >= 7:
                 # Check if the session is ending soon
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "asia_session_close_time",
                    "current_hour": current_hour,
                    "entry_price": ctx.position["entry_price"]
                })
            elif ctx.position["direction"] == "short" and current_hour >= 7:
                 # Check if the session is ending soon
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "asia_session_close_time",
                    "current_hour": current_hour,
                    "entry_price": ctx.position["entry_price"]
                })
            # If the position has been held for too long (e.g., more than 1.5x declared_hold_seconds), exit
            # This is a fallback to avoid holding positions indefinitely if the time-based exit is missed
            if ctx.bar_index - ctx.position.get("entry_bar_index", 0) > (self.METADATA["declared_hold_seconds"] / ctx.market.get("bar_duration_seconds", 3600)) * 1.5:
                 return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "max_hold_time_exceeded",
                    "bars_held": ctx.bar_index - ctx.position.get("entry_bar_index", 0)
                })
            return None # Hold existing position

        # Entry logic: Only attempt entry during the Asia session handoff (e.g., UTC 8-9)
        # We look for the impulse from the previous Asia session (UTC 0-8)
        if current_hour == 8 and not self.daily_entry_made: # Try to enter at 8 AM UTC
            # Ensure we have enough bars for the previous Asia session (0-7 UTC)
            # This means we need 8 bars ending at hour 7
            if len(closes) < 8:
                return None

            # Calculate the impulse of the previous Asia session (bars from UTC 0 to 7)
            # The closes array is oldest first. If current_hour is 8, closes[-1] is current bar,
            # closes[-2] is hour 7, ..., closes[-9] is hour 0.
            # So, previous Asia session bars are closes[-9:-1]
            asia_session_closes = closes[-9:-1]
            asia_session_opens = opens[-9:-1]
            asia_session_highs = highs[-9:-1]
            asia_session_lows = lows[-9:-1]

            if len(asia_session_closes) != self.session_period:
                return None # Not enough data for a full Asia session

            session_open_price = asia_session_opens[0]
            session_close_price = asia_session_closes[-1]
            session_high = max(asia_session_highs)
            session_low = min(asia_session_lows)

            # Calculate the session's directional impulse
            price_change = session_close_price - session_open_price
            session_range = session_high - session_low

            if session_range == 0:
                return None

            # Normalize price change by session range to get directional impulse
            directional_impulse = price_change / session_range
            
            # Additional filter: ensure current bar is confirming the direction
            # If the session was bullish, current bar should be strong close above open
            # If the session was bearish, current bar should be strong close below open
            current_bar_open = ctx.bar.open
            current_bar_close = ctx.bar.close

            # Bullish impulse confirmation
            if directional_impulse > 0.3 and (current_bar_close - current_bar_open) / current_bar_open > self.entry_threshold_pct:
                self.last_trade_bar = ctx.bar_index
                self.daily_entry_made = True
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_session_bullish_handoff_confirmed",
                        "session_open": session_open_price,
                        "session_close": session_close_price,
                        "directional_impulse": directional_impulse,
                        "current_bar_change_pct": (current_bar_close - current_bar_open) / current_bar_open,
                        "current_hour": current_hour
                    }
                )
            # Bearish impulse confirmation
            elif directional_impulse < -0.3 and (current_bar_open - current_bar_close) / current_bar_open > self.entry_threshold_pct:
                self.last_trade_bar = ctx.bar_index
                self.daily_entry_made = True
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_session_bearish_handoff_confirmed",
                        "session_open": session_open_price,
                        "session_close": session_close_price,
                        "directional_impulse": directional_impulse,
                        "current_bar_change_pct": (current_bar_open - current_bar_close) / current_bar_open,
                        "current_hour": current_hour
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for adaptive logic if needed in the future
        # For now, just reset last_trade_bar for cooldown
        self.last_trade_bar = ctx.bar_index
        pass