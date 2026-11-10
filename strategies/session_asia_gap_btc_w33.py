from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionReversion(Strategy):
    METADATA = {
        "name": "Asia Session Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Up to 3 hours within the session
        "warmup_bars": 24,  # Need enough bars for prior session close and current session open
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_session_start_hour = 0  # UTC 0
        self.asia_session_end_hour = 8    # UTC 8
        self.min_gap_bps = 25.0           # Minimum gap in bps to consider
        self.max_gap_bps = 150.0          # Max gap to consider (too large might be a breakout)
        self.gap_reversion_threshold_bps = 5.0 # How much price needs to move into the gap to confirm
        self.last_trade_bar_index = -1
        self.cooldown_bars = 6 # Wait 6 bars after a trade before new entry
        self.prior_session_close = None
        self.current_session_open_price = None
        self.session_initialized = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        current_hour = ctx.bar.timestamp.hour

        # Check if we are in a position and need to exit before session end
        if ctx.has_position():
            # Exit if we are approaching the end of the Asia session
            # Give a small buffer, e.g., exit 1 bar before the session officially ends
            if current_hour == self.asia_session_end_hour - 1 and ctx.bar.timestamp.minute >= 55:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "exit_before_asia_session_end",
                    "current_hour": current_hour,
                    "session_end_hour": self.asia_session_end_hour
                })
            # Or if we have held for too long (e.g., 3 hours)
            if ctx.bar_index - ctx.position["entry_bar_index"] > (self.METADATA["declared_hold_seconds"] / 3600):
                 return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_due_to_max_hold_time",
                    "bars_held": ctx.bar_index - ctx.position["entry_bar_index"]
                })
            return None # Hold position if not exiting

        # Cooldown period after a previous trade
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Logic for identifying prior session close and current session open
        # We define a "session" as UTC 0-8 (Asia), UTC 8-16 (Europe), UTC 16-0 (US)
        # We are interested in the transition from US session to Asia session.
        # The prior session close is the close of the last bar of the US session (23:00 UTC).
        # The current session open is the open of the first bar of the Asia session (00:00 UTC).

        # Detect start of Asia session (UTC 00:00)
        if current_hour == self.asia_session_start_hour and not self.session_initialized:
            # Look back to find the close of the last bar of the previous US session (UTC 23:00)
            # This requires at least 1 bar from the previous hour (23:00)
            # and enough bars to get the open of the current bar (00:00)
            if len(ctx.closes(2)) < 2:
                return None # Not enough data yet

            self.prior_session_close = ctx.closes(2)[0] # Close of previous hour (23:00 UTC)
            self.current_session_open_price = ctx.bar.open # Open of current bar (00:00 UTC)
            self.session_initialized = True
            # We don't trade on the first bar of the session, just set up the context
            return None

        # Reset session context if we are outside the Asia session
        if current_hour == self.asia_session_end_hour:
            self.session_initialized = False
            self.prior_session_close = None
            self.current_session_open_price = None
            return None

        # Only execute logic if within Asia session and context is set
        if not self.session_initialized or self.prior_session_close is None or self.current_session_open_price is None:
            return None

        # Calculate gap between prior session close and current session open
        gap_raw = self.current_session_open_price - self.prior_session_close
        gap_bps = (gap_raw / self.prior_session_close) * 10000

        # Check if gap is significant
        if not (self.min_gap_bps <= abs(gap_bps) <= self.max_gap_bps):
            return None

        # Check for gap reversion opportunity
        # Long if price gapped down and is trying to fill it
        if gap_bps < 0: # Gapped down
            # Current price (close) moves up into the gap
            if ctx.bar.close > self.current_session_open_price and \
               ctx.bar.close > (self.current_session_open_price + self.prior_session_close) / 2: # Check if it's past the midpoint
                # Ensure current price is still below prior session close
                if ctx.bar.close < self.prior_session_close:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("long", confidence=0.75, metadata={
                        "reason": "asia_session_gap_down_reversion",
                        "gap_bps": gap_bps,
                        "prior_session_close": self.prior_session_close,
                        "current_session_open": self.current_session_open_price,
                        "current_close": ctx.bar.close
                    })
        # Short if price gapped up and is trying to fill it
        elif gap_bps > 0: # Gapped up
            # Current price (close) moves down into the gap
            if ctx.bar.close < self.current_session_open_price and \
               ctx.bar.close < (self.current_session_open_price + self.prior_session_close) / 2: # Check if it's past the midpoint
                # Ensure current price is still above prior session close
                if ctx.bar.close > self.prior_session_close:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("short", confidence=0.75, metadata={
                        "reason": "asia_session_gap_up_reversion",
                        "gap_bps": gap_bps,
                        "prior_session_close": self.prior_session_close,
                        "current_session_open": self.current_session_open_price,
                        "current_close": ctx.bar.close
                    })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic for now, just logging
        pass