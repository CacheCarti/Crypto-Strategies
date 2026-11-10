from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapReversion(Strategy):
    METADATA = {
        "name": "Session Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours, targeting prior close
        "warmup_bars": 24, # Need enough bars for prior day's close and open
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_signal_bar = -1
        self.cooldown_bars = 6 # Wait 6 hours (6 bars) after a trade
        self.last_session_close = None
        self.session_open_price = None
        self.session_active = False
        self.gap_threshold_bps = 20 # Minimum gap to consider, in bps
        self.continuation_threshold_bps = 5 # How much continuation is considered "lack of continuation"

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Session boundaries:
        # Late US session: UTC 18-24 (inclusive of 18, exclusive of 0 next day)
        # Prior session close is end of UTC 17-18 (bar that ends at 18:00)
        # Next session open is start of UTC 18-19 (bar that starts at 18:00)

        # Detect the start of the late US session (UTC 18:00)
        if current_hour == 18 and not self.session_active:
            # This is the first bar of the new session
            # We need the close of the bar that ended at 18:00 (i.e., the close of 17:00 bar)
            # The close at `ctx.closes(-2)` would be the close of the bar that ended at 17:00 UTC.
            # However, since `closes` is oldest first, `closes[-2]` is the close before the last one.
            # We need the bar *prior* to the current one for its close.
            # The 18:00 bar's open is ctx.bar.open
            
            # To get the close of the bar ending at 18:00 (i.e., the 17:00 bar's close)
            # we need the close of the bar *before* the current bar started.
            # ctx.closes(2) gives [close_t-1, close_t]
            # So, closes[-2] is the close of the bar that ended at 17:00 UTC.
            if len(closes) >= 2:
                self.last_session_close = closes[-2] # Close of the bar ending at UTC 17:00
                self.session_open_price = ctx.bar.open # Open of the bar starting at UTC 18:00
                self.session_active = True
            else:
                return None # Not enough history to determine prior close

        # Reset session_active at UTC 00:00 for the next day
        if current_hour == 0 and self.session_active:
            self.session_active = False
            self.last_session_close = None
            self.session_open_price = None
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_price": ctx.bar.close})

        # Logic for trading within the late US session (UTC 18-24)
        if self.session_active and self.last_session_close is not None and self.session_open_price is not None:
            if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
                return None # Cooldown active

            # Calculate gap from prior session close to current session open
            gap_bps = ((self.session_open_price - self.last_session_close) / self.last_session_close) * 10000

            # Check if there's a significant gap
            if abs(gap_bps) >= self.gap_threshold_bps:
                # Check for lack of continuation in the current bar
                # If gap is up, and current bar's close is not significantly higher than open
                # If gap is down, and current bar's close is not significantly lower than open
                
                continuation_bps_current_bar = ((ctx.bar.close - ctx.bar.open) / ctx.bar.open) * 10000

                # Long signal: Gap down, but no significant continuation lower
                if gap_bps < 0 and continuation_bps_current_bar > -self.continuation_threshold_bps:
                    if not ctx.has_position():
                        self.last_signal_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "session_gap_down_reversion",
                                "prior_session_close": self.last_session_close,
                                "session_open_price": self.session_open_price,
                                "gap_bps": round(gap_bps, 2),
                                "current_bar_continuation_bps": round(continuation_bps_current_bar, 2),
                                "entry_price": ctx.bar.close
                            }
                        )

                # Short signal: Gap up, but no significant continuation higher
                elif gap_bps > 0 and continuation_bps_current_bar < self.continuation_threshold_bps:
                    if not ctx.has_position():
                        self.last_signal_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            metadata={
                                "reason": "session_gap_up_reversion",
                                "prior_session_close": self.last_session_close,
                                "session_open_price": self.session_open_price,
                                "gap_bps": round(gap_bps, 2),
                                "current_bar_continuation_bps": round(continuation_bps_current_bar, 2),
                                "entry_price": ctx.bar.close
                            }
                        )
        
        # If we have a position and it's getting close to UTC 0, consider exiting.
        # This is handled by the `current_hour == 0` check above.

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any state that should be fresh after a position closes
        pass