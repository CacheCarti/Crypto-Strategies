from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapReversion(Strategy):
    METADATA = {
        "name": "Session Gap Reversion BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold up to 4 hours
        "warmup_bars": 24, # Need enough bars for session tracking
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13
        self.us_session_end_hour = 21 # Exits before this hour
        self.min_gap_bps = 20.0       # Minimum gap in basis points to consider
        self.continuation_threshold_bps = 10.0 # How much price moves further from gap
        self.cooldown_bars = 6        # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars - 1
        self.prior_session_close = None
        self.session_open_price = None
        self.trade_taken_today = False
        self.current_day = -1 # To track day changes

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(2)
        opens = ctx.opens(1)

        if len(closes) < 2 or len(opens) < 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day_of_week = ctx.bar.timestamp.weekday() # Monday 0, Sunday 6

        # Reset daily state at the start of a new day (e.g., UTC midnight)
        if ctx.bar.timestamp.day != self.current_day:
            self.trade_taken_today = False
            self.current_day = ctx.bar.timestamp.day
            # Also reset prior_session_close, as it's specific to the previous day's US session
            self.prior_session_close = None 

        # Track prior US session close
        if current_hour == self.us_session_end_hour - 1: # One hour before session end
            self.prior_session_close = ctx.bar.close

        # Check if we are in the US session
        is_us_session_start = current_hour == self.us_session_start_hour

        # If it's the start of the US session, record the open price
        if is_us_session_start:
            self.session_open_price = ctx.bar.open
        
        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Exit logic: Close position before session end if open
        if ctx.has_position() and current_hour == self.us_session_end_hour - 1:
            if ctx.position_direction() == "long" or ctx.position_direction() == "short":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        # Entry logic
        if not ctx.has_position() and not self.trade_taken_today:
            if self.prior_session_close is not None and self.session_open_price is not None:
                # Only consider trading if we are within the US session, but not right at the start
                if current_hour >= self.us_session_start_hour + 1 and current_hour < self.us_session_end_hour - 1:
                    gap_to_prior_close_bps = ((self.session_open_price - self.prior_session_close) / self.prior_session_close) * 10000

                    # Check for a significant gap
                    if abs(gap_to_prior_close_bps) > self.min_gap_bps:
                        
                        # Check for lack of continuation
                        # If price moved further away from prior close after opening, it's continuation
                        # We want price to have moved back towards prior close or not move much
                        
                        # Current price relative to gap direction
                        price_movement_from_open = ctx.bar.close - self.session_open_price
                        
                        signal_direction = None
                        reason = ""

                        if gap_to_prior_close_bps > 0: # Opened higher than prior close (up gap)
                            # Expect price to move down towards prior close
                            # If it moved up even more, it's continuation. We want it to not continue up, or reverse.
                            if price_movement_from_open < self.continuation_threshold_bps / 10000 * self.session_open_price: # Price did not continue significantly higher
                                signal_direction = "short"
                                reason = "up_gap_reversion_no_continuation"
                        elif gap_to_prior_close_bps < 0: # Opened lower than prior close (down gap)
                            # Expect price to move up towards prior close
                            # If it moved down even more, it's continuation. We want it to not continue down, or reverse.
                            if price_movement_from_open > -self.continuation_threshold_bps / 10000 * self.session_open_price: # Price did not continue significantly lower
                                signal_direction = "long"
                                reason = "down_gap_reversion_no_continuation"
                        
                        if signal_direction:
                            self.trade_taken_today = True
                            self.last_trade_bar = ctx.bar_index
                            confidence = 0.7 # Base confidence
                            
                            return ctx.signal(
                                signal_direction,
                                confidence=confidence,
                                metadata={
                                    "reason": reason,
                                    "gap_bps": round(gap_to_prior_close_bps, 2),
                                    "session_open": self.session_open_price,
                                    "prior_close": self.prior_session_close,
                                    "current_close": ctx.bar.close,
                                    "current_hour": current_hour
                                }
                            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this simple strategy, but could be added here
        pass