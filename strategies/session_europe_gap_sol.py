from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeGapReversionSOL(Strategy):
    METADATA = {
        "name": "EuropeGapReversionSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold time up to 4 hours
        "warmup_bars": 24 * 2,  # Need at least two full days of data for prior session close
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7
        self.entry_hour_end = 9   # UTC 9 (allow entry up to this hour)
        self.exit_hour = 12       # UTC 12 (exit by this hour)
        self.cooldown_bars = 6    # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trade

        # Store prior session's closing price
        self.prior_session_close = None
        self.last_session_end_bar_index = -1

        # Parameters for gap calculation
        self.min_gap_bps = 50.0  # Minimum gap in bps to consider for a trade
        self.continuation_threshold_bps = 25.0 # How much the price can move in the gap direction without invalidating

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Logic to capture prior session's close (UTC 12-13 boundary)
        # We define a "session" as roughly 00:00 UTC to 12:00 UTC for this strategy
        # The "prior session close" will be the close at 12:00 UTC of the previous day
        # Or, more simply, the close at the end of our "session" (UTC 12)
        if current_hour == self.exit_hour and ctx.bar_index > self.last_session_end_bar_index:
            self.prior_session_close = ctx.bar.close
            self.last_session_end_bar_index = ctx.bar_index

        # If we have a position, check for exit conditions
        if ctx.has_position():
            # Exit by the end of the session
            if current_hour >= self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "time_based_exit",
                    "exit_hour": self.exit_hour,
                    "current_price": ctx.bar.close
                })
            # Otherwise, hold the position
            return ctx.signal("hold")

        # Entry logic
        # Only consider entries within the entry window and after cooldown
        if not (self.entry_hour_start <= current_hour < self.entry_hour_end) or \
           (ctx.bar_index - self.last_trade_bar < self.cooldown_bars):
            return None

        # Need prior session close data
        if self.prior_session_close is None:
            return None

        # Calculate the gap from prior session close to current open
        gap_bps = (ctx.bar.open - self.prior_session_close) / self.prior_session_close * 10000

        # Check for gap conditions and continuation failure
        signal_direction = None
        reason = ""
        confidence = 0.0

        if gap_bps > self.min_gap_bps:  # Large upward gap
            # Check for lack of continuation downwards
            # If the current close is still significantly above the prior close, it's not reverting yet
            # We want to see the market fail to continue higher
            if ctx.bar.close < self.prior_session_close + (self.continuation_threshold_bps / 10000.0 * self.prior_session_close):
                signal_direction = "short"
                reason = "large_up_gap_reversion_no_continuation"
                confidence = min(0.7, abs(gap_bps) / 200.0) # Scale confidence by gap size
        elif gap_bps < -self.min_gap_bps: # Large downward gap
            # Check for lack of continuation upwards
            if ctx.bar.close > self.prior_session_close - (self.continuation_threshold_bps / 10000.0 * self.prior_session_close):
                signal_direction = "long"
                reason = "large_down_gap_reversion_no_continuation"
                confidence = min(0.7, abs(gap_bps) / 200.0) # Scale confidence by gap size

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "prior_session_close": self.prior_session_close,
                    "current_open": ctx.bar.open,
                    "current_close": ctx.bar.close,
                    "gap_bps": gap_bps,
                    "entry_hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this simple strategy, but good to have
        pass