from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionGapReversion(Strategy):
    METADATA = {
        "name": "SOL Session Gap Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended for 1-hour bars, exits before session end
        "warmup_bars": 24, # Need enough bars for 24-hour lookback for session data
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_session_close = None
        self.session_open_price = None
        self.traded_today = False
        self.cooldown_bars = 0
        self.cooldown_duration = 3 # Bars to wait after a trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Manage cooldown
        if self.cooldown_bars > 0:
            self.cooldown_bars -= 1
            return None

        # --- Session Management ---
        # US session: UTC 13-21 (8 hours)
        # Session close at 20:00 UTC (bar for 20:00-21:00)
        # Session open at 13:00 UTC (bar for 13:00-14:00)

        # End of US session (UTC 20:00 bar, before 21:00)
        if current_hour == 20:
            if ctx.has_position():
                # Exit any open position before the session truly closes
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour, "current_price": current_close})
            self.last_session_close = current_close
            self.traded_today = False # Reset for next session
            self.session_open_price = None # Reset session open
            return None # Do not trade at session end

        # Start of US session (UTC 13:00 bar)
        if current_hour == 13:
            self.traded_today = False # Reset for new session
            self.session_open_price = ctx.bar.open
            return None

        # --- Trading Logic (only within US session, after 13:00 and before 20:00) ---
        if 13 < current_hour < 20 and not self.traded_today:
            if self.last_session_close is None or self.session_open_price is None:
                return None

            gap_magnitude_bps = ((self.session_open_price - self.last_session_close) / self.last_session_close) * 10000

            # Only consider significant gaps
            if abs(gap_magnitude_bps) < 50: # e.g., 50 bps minimum gap
                return None

            # Check for lack of continuation and reversion opportunity
            # We want to trade if the price moves against the gap direction, suggesting reversion
            
            # Simple check: current price is moving back towards last_session_close
            # If gap was positive (open > close), we look for current_close < session_open_price
            # If gap was negative (open < close), we look for current_close > session_open_price

            if gap_magnitude_bps > 0: # Positive gap (opened higher)
                # Look for a short opportunity if price fails to continue higher and moves lower
                # Entry condition: current_close has dipped below the session open, suggesting reversion
                if current_close < self.session_open_price:
                    # And it's still above the last session close (room for reversion)
                    if current_close > self.last_session_close: # Reversion target is last_session_close
                        self.traded_today = True
                        self.cooldown_bars = self.cooldown_duration
                        return ctx.signal("short", confidence=0.75,
                                          metadata={
                                              "reason": "positive_gap_reversion_short",
                                              "last_session_close": self.last_session_close,
                                              "session_open": self.session_open_price,
                                              "gap_bps": round(gap_magnitude_bps, 2),
                                              "current_price": current_close,
                                              "target_price_approx": self.last_session_close
                                          })
            elif gap_magnitude_bps < 0: # Negative gap (opened lower)
                # Look for a long opportunity if price fails to continue lower and moves higher
                # Entry condition: current_close has risen above the session open, suggesting reversion
                if current_close > self.session_open_price:
                    # And it's still below the last session close (room for reversion)
                    if current_close < self.last_session_close: # Reversion target is last_session_close
                        self.traded_today = True
                        self.cooldown_bars = self.cooldown_duration
                        return ctx.signal("long", confidence=0.75,
                                          metadata={
                                              "reason": "negative_gap_reversion_long",
                                              "last_session_close": self.last_session_close,
                                              "session_open": self.session_open_price,
                                              "gap_bps": round(gap_magnitude_bps, 2),
                                              "current_price": current_close,
                                              "target_price_approx": self.last_session_close
                                          })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after any position closes, regardless of reason
        self.cooldown_bars = self.cooldown_duration
        # Log closing details if needed
        # ctx.logger.info(f"Position closed: {position}")