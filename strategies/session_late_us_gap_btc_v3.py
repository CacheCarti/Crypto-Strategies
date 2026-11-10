from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcLateUsSessionReversion(Strategy):
    METADATA = {
        "name": "BTC Late US Session Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-3 hours average hold
        "warmup_bars": 20,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_gap_bps = 50.0  # Minimum gap size to consider
        self.max_continuation_bars = 2 # How many bars after open to check for continuation
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade
        self.prior_session_close = None
        self.session_open_price = None
        self.session_direction = None # "up_gap", "down_gap"
        self.session_gap_bar_index = -1
        self.entry_triggered_today = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        if len(closes) < self.METADATA["warmup_bars"] + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # Reset for new day/session if we're past UTC 0
        if current_hour == 0 and not self.entry_triggered_today:
            self.prior_session_close = None
            self.session_open_price = None
            self.session_direction = None
            self.session_gap_bar_index = -1
            self.entry_triggered_today = False # Reset daily entry flag

        # --- Session Boundary Logic ---
        # Define Late US session start (e.g., 18 UTC)
        late_us_session_start_hour = 18

        # If it's the first bar of the late US session
        if current_hour == late_us_session_start_hour and self.session_open_price is None:
            # Store the close of the previous bar as prior session close
            # Assuming the bar at 18:00 UTC represents the open of the 18-19 period.
            # The prior close is the close of the 17:00-18:00 bar.
            self.prior_session_close = ctx.opens(2)[0] # Open of current bar is close of previous
            self.session_open_price = ctx.bar.open
            self.session_gap_bar_index = current_bar_index
            self.entry_triggered_today = False # Allow new entry for this session
            
            if self.prior_session_close is not None:
                gap_bps = ((self.session_open_price - self.prior_session_close) / self.prior_session_close) * 10000
                if gap_bps > self.min_gap_bps:
                    self.session_direction = "up_gap"
                elif gap_bps < -self.min_gap_bps:
                    self.session_direction = "down_gap"
                else:
                    self.session_direction = None # No significant gap

        # --- Trading Logic ---
        if ctx.has_position():
            # Exit before UTC 0 if position is still open
            if current_hour == 23 and ctx.bar.timestamp.minute >= 55: # Close in the last 5 minutes of the hour
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_price": ctx.bar.close})
            return ctx.signal("hold")

        # Cooldown check
        if current_bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only consider trades within the late US session (e.g., 18-23 UTC)
        if not (late_us_session_start_hour <= current_hour < 24):
            return None
        
        # Only trade if a gap was detected at the session open and no entry yet today
        if self.session_direction is None or self.prior_session_close is None or self.entry_triggered_today:
            return None

        # Check for continuation failure within max_continuation_bars
        bars_since_gap_open = current_bar_index - self.session_gap_bar_index
        if not (1 <= bars_since_gap_open <= self.max_continuation_bars):
            return None

        current_price = ctx.bar.close

        # Gap Up Reversion (price opened higher, but failed to continue up)
        if self.session_direction == "up_gap":
            # If price has fallen back towards or below the prior close
            if current_price < self.session_open_price * (1 - self.min_gap_bps/2 / 10000.0): # Price dropped significantly from open
                if current_price < self.prior_session_close: # Price is below prior session close
                     # Signal short to revert to or below the prior close
                    self.last_trade_bar = current_bar_index
                    self.entry_triggered_today = True
                    return ctx.signal(
                        "short",
                        confidence=0.65,
                        metadata={
                            "reason": "up_gap_reversion_failed_continuation",
                            "session_open": self.session_open_price,
                            "prior_close": self.prior_session_close,
                            "current_price": current_price,
                            "gap_bps": ((self.session_open_price - self.prior_session_close) / self.prior_session_close) * 10000,
                            "bars_since_gap_open": bars_since_gap_open
                        }
                    )

        # Gap Down Reversion (price opened lower, but failed to continue down)
        elif self.session_direction == "down_gap":
            # If price has risen back towards or above the prior close
            if current_price > self.session_open_price * (1 + self.min_gap_bps/2 / 10000.0): # Price rose significantly from open
                if current_price > self.prior_session_close: # Price is above prior session close
                    # Signal long to revert to or above the prior close
                    self.last_trade_bar = current_bar_index
                    self.entry_triggered_today = True
                    return ctx.signal(
                        "long",
                        confidence=0.65,
                        metadata={
                            "reason": "down_gap_reversion_failed_continuation",
                            "session_open": self.session_open_price,
                            "prior_close": self.prior_session_close,
                            "current_price": current_price,
                            "gap_bps": ((self.session_open_price - self.prior_session_close) / self.prior_session_close) * 10000,
                            "bars_since_gap_open": bars_since_gap_open
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session-specific state after a position closes
        # This allows for a new entry if conditions align later in the same session
        # However, the `entry_triggered_today` flag prevents multiple entries on the same day/session.
        pass