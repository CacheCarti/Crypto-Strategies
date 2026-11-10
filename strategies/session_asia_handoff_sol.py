from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionImpulseSOL(Strategy):
    METADATA = {
        "name": "AsiaSessionImpulseSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 8,  # Max hold until UTC 8
        "warmup_bars": 24, # Need 8 bars for session, plus a few for context
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 00:00
        self.session_end_hour = 8    # UTC 08:00
        self.entry_cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar_index = -self.entry_cooldown_bars * 2 # Initialize to allow first trade

        # Store daily session data
        self.daily_session_data = {} # {date: {"high": None, "low": None, "open": None, "close": None, "bars": []}}

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        current_hour = ctx.bar.timestamp.hour
        current_date = ctx.bar.timestamp.date()
        closes = ctx.closes(2)
        opens = ctx.opens(2)
        highs = ctx.highs(2)
        lows = ctx.lows(2)

        if len(closes) < 2:
            return None

        # Daily session data management
        if current_date not in self.daily_session_data:
            self.daily_session_data[current_date] = {
                "high": -math.inf, "low": math.inf, "open": None, "close": None, "bars": []
            }
        
        session_data = self.daily_session_data[current_date]

        # Update session data if within Asia session
        if self.session_start_hour <= current_hour < self.session_end_hour:
            if session_data["open"] is None:
                session_data["open"] = ctx.bar.open
            session_data["high"] = max(session_data["high"], ctx.bar.high)
            session_data["low"] = min(session_data["low"], ctx.bar.low)
            session_data["close"] = ctx.bar.close
            session_data["bars"].append(ctx.bar.close)

        # Check for trade entry conditions
        if not ctx.has_position() and (ctx.bar_index - self.last_trade_bar_index > self.entry_cooldown_bars):
            # Entry logic: after Asia session ends, at the handoff
            if current_hour == self.session_end_hour:
                # Need at least 4 bars of session data to be meaningful
                if session_data["open"] is not None and len(session_data["bars"]) >= 4:
                    session_range = session_data["high"] - session_data["low"]
                    session_close = session_data["close"]
                    session_open = session_data["open"]

                    # Calculate session impulse
                    impulse = session_close - session_open

                    # Check for follow-through confirmation in the current bar (first bar after session)
                    # We look for a move in the direction of the session impulse
                    # and a close confirming that direction.
                    
                    # Long condition: Asia session was bullish and current bar confirms
                    if impulse > 0 and ctx.bar.close > ctx.bar.open and ctx.bar.close > session_close:
                        # Ensure the current bar has moved beyond the session high or close
                        # and that the session itself showed decent range.
                        if session_range > 0.005 * session_open: # Session range at least 0.5%
                            self.last_trade_bar_index = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.7,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "asia_session_bullish_impulse_confirmed",
                                    "session_open": session_open,
                                    "session_close": session_close,
                                    "session_impulse": impulse,
                                    "session_range": session_range,
                                    "current_bar_close": ctx.bar.close
                                }
                            )
                    
                    # Short condition: Asia session was bearish and current bar confirms
                    elif impulse < 0 and ctx.bar.close < ctx.bar.open and ctx.bar.close < session_close:
                        if session_range > 0.005 * session_open: # Session range at least 0.5%
                            self.last_trade_bar_index = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.7,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "asia_session_bearish_impulse_confirmed",
                                    "session_open": session_open,
                                    "session_close": session_close,
                                    "session_impulse": impulse,
                                    "session_range": session_range,
                                    "current_bar_close": ctx.bar.close
                                }
                            )
        
        # Exit logic: Close position by the end of the session handoff period (e.g., UTC 8)
        # Or if a position is open and we are approaching the next session's start (e.g., UTC 0 of next day)
        if ctx.has_position():
            # Exit at the end of the specified hold window, or if market reverses strongly
            # We enforce a hard exit at self.session_end_hour + 1 (9 AM UTC) at the latest if still open
            # This ensures we don't hold past the intended handoff analysis.
            if current_hour == (self.session_end_hour + 1) % 24: # One hour after session close, e.g., 09:00 UTC
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_based_exit_end_of_handoff_window",
                        "current_hour": current_hour,
                        "position_direction": ctx.position_direction(),
                        "entry_price": ctx.position["entry_price"]
                    }
                )
            
            # Additional structural stop: if price moves significantly against us after entry
            # This is a dynamic stop beyond the declared_sl_bps to react quicker to structure breaks
            if ctx.position_direction() == "long":
                # If price drops below the session close after a long entry, consider exiting
                if ctx.bar.close < session_data["close"] * 0.995: # 0.5% below session close
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "structural_stop_long_below_session_close",
                            "session_close": session_data["close"],
                            "current_close": ctx.bar.close
                        }
                    )
            elif ctx.position_direction() == "short":
                # If price rises above the session close after a short entry, consider exiting
                if ctx.bar.close > session_data["close"] * 1.005: # 0.5% above session close
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "structural_stop_short_above_session_close",
                            "session_close": session_data["close"],
                            "current_close": ctx.bar.close
                        }
                    )

        # Clear session data for previous days to prevent memory leak and ensure fresh daily data
        # This is a simple cleanup, could be more sophisticated for very long backtests
        if len(self.daily_session_data) > 2: # Keep today and yesterday
            oldest_date = min(self.daily_session_data.keys())
            if oldest_date < current_date:
                del self.daily_session_data[oldest_date]

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, if needed, or adjust it
        # For this strategy, cooldown is handled at entry attempt.
        pass