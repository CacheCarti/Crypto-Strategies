from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanSessionGapReversion(Strategy):
    METADATA = {
        "name": "EuropeanSessionGapReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 180.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold until end of session or earlier
        "warmup_bars": 24, # Need at least 24 bars for prior day's close
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.prior_session_close = None
        self.session_open_price = None
        self.traded_today = False
        self.last_trade_bar_index = -1
        self.cooldown_bars = 4 # Cooldown period after a trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(25) # Need prior day's close, so 24 bars back for 1h chart
        opens = ctx.opens(2)
        if len(closes) < self.METADATA["warmup_bars"] or len(opens) < 2:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset daily state at the start of a new trading day (e.g., UTC 00:00 or after trading hours)
        if current_hour == 0 and not self.traded_today:
            self.traded_today = False
            self.prior_session_close = None
            self.session_open_price = None

        # --- Session Boundary Logic ---
        # Define the European session (e.g., UTC 7-12)
        EUROPEAN_SESSION_START = 7
        EUROPEAN_SESSION_END = 12 # Exit by this hour (inclusive)

        # Capture prior session close (e.g., end of previous day's trading or specific session)
        # For simplicity, let's consider the close of the bar just before European session starts as "prior session close"
        # Or, more robustly, the close of the previous day (UTC 00:00 bar close for a full day)
        # Let's simplify: prior day's close is the close 24 bars ago if we are at the start of the new day.
        # A more precise way: store the close of the last bar of the previous "trading day"
        
        # At UTC 7, capture the open price of the current bar as session open
        # and the close of the bar at UTC 6 as the "prior session close"
        if current_hour == EUROPEAN_SESSION_START:
            self.session_open_price = ctx.bar.open
            # The close of the bar at hour 6 (index -2) is our prior session close
            self.prior_session_close = closes[-2] 
            self.traded_today = False # Reset for the new session

        # --- Trading Logic ---
        if ctx.has_position():
            # Exit by end of session if still holding
            if current_hour >= EUROPEAN_SESSION_END:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None # Hold position if within session

        # Only consider entries within the European session, and if not traded today, and after cooldown
        if EUROPEAN_SESSION_START <= current_hour < EUROPEAN_SESSION_END and \
           not self.traded_today and \
           self.prior_session_close is not None and \
           self.session_open_price is not None and \
           (ctx.bar_index - self.last_trade_bar_index) > self.cooldown_bars:

            gap_magnitude_bps = (self.session_open_price - self.prior_session_close) / self.prior_session_close * 10000
            current_price = ctx.bar.close
            
            # Define a threshold for a "large" gap
            GAP_THRESHOLD_BPS = 50.0 # 0.5% gap
            
            # Continuation check: has the price moved further in the gap direction?
            # We want to trade back if there's a gap AND it's not continuing significantly
            
            # Long condition: Gap down, but not continuing lower
            if gap_magnitude_bps < -GAP_THRESHOLD_BPS: # Large gap down
                # Check if price has moved back up towards the prior close, or if it's not continuing down
                # If current_price is recovering towards prior_session_close
                if current_price > self.session_open_price * (1 - GAP_THRESHOLD_BPS/2 / 10000): # Recovered slightly from open
                    self.traded_today = True
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "europe_session_gap_down_reversion",
                            "gap_bps": round(gap_magnitude_bps, 2),
                            "prior_close": round(self.prior_session_close, 2),
                            "session_open": round(self.session_open_price, 2),
                            "current_price": round(current_price, 2),
                            "current_hour": current_hour
                        }
                    )
            
            # Short condition: Gap up, but not continuing higher
            elif gap_magnitude_bps > GAP_THRESHOLD_BPS: # Large gap up
                # Check if price has moved back down towards the prior close, or if it's not continuing up
                # If current_price is recovering towards prior_session_close
                if current_price < self.session_open_price * (1 + GAP_THRESHOLD_BPS/2 / 10000): # Dropped slightly from open
                    self.traded_today = True
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "europe_session_gap_up_reversion",
                            "gap_bps": round(gap_magnitude_bps, 2),
                            "prior_close": round(self.prior_session_close, 2),
                            "session_open": round(self.session_open_price, 2),
                            "current_price": round(current_price, 2),
                            "current_hour": current_hour
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy on close, but can be added.
        # For example, logging the outcome or adjusting confidence based on recent performance.
        pass