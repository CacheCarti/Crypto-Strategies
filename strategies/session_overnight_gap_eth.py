from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthOvernightGapReversion(Strategy):
    METADATA = {
        "name": "EthOvernightGapReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hours, exit before US session
        "warmup_bars": 24, # Need at least 24 bars for prior day's close
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -9999
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.prior_session_close = None
        self.prior_session_close_bar_index = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Update prior session close (from UTC 0-6 window)
        # We define the "prior session" as the UTC 0-6 window.
        # The close of this session is the close of the 6 AM UTC bar.
        if current_hour == 6: # At 6 AM UTC, the prior session (0-6) has just closed
            self.prior_session_close = ctx.bar.close
            self.prior_session_close_bar_index = ctx.bar_index
            # Reset the prior session close if we are past the entry window
            # This ensures we only capture a new prior close once per day
            if ctx.bar_index > self.prior_session_close_bar_index + 18: # After 6am + 18 hours = 00:00 UTC
                self.prior_session_close = None
                self.prior_session_close_bar_index = -1

        # Entry logic: Between 7 AM UTC and 12 PM UTC (after overnight session, before US session fully kicks in)
        # And only if we have a prior session close from today
        is_entry_window = 7 <= current_hour <= 12
        
        # Exit logic: If current time is 13:00 UTC or later, and we have a position, flat it.
        # This is to ensure we are out before the US session is in full swing.
        if ctx.has_position() and current_hour >= 13:
            return ctx.signal("flat", confidence=0.8, metadata={"reason": "time_based_exit_before_us_session", "current_hour": current_hour})

        if not is_entry_window or ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        if ctx.has_position():
            return None # Already in a position

        if self.prior_session_close is None:
            return None # No prior session close established yet

        # The "next session open" is effectively the current bar's open
        # We are looking for a gap between the prior session close and the current bar's open
        gap = ctx.bar.open - self.prior_session_close
        gap_pct = (gap / self.prior_session_close) * 10000 # in bps

        # Define gap thresholds (e.g., 0.1% to 1.0% gap)
        min_gap_bps = 10.0 # 0.1%
        max_gap_bps = 100.0 # 1.0%

        # Check for gap reversion opportunity
        # If there's a significant positive gap up and current price is falling back towards prior close
        if gap_pct > min_gap_bps and gap_pct < max_gap_bps:
            # Look for lack of continuation: current close is below open, and price is moving towards prior_session_close
            if ctx.bar.close < ctx.bar.open and ctx.bar.close < self.prior_session_close + (gap * 0.5): # Price has retraced at least 50% of the gap
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "overnight_gap_up_reversion",
                        "gap_bps": gap_pct,
                        "prior_session_close": self.prior_session_close,
                        "current_open": ctx.bar.open,
                        "current_close": ctx.bar.close,
                        "entry_hour": current_hour
                    }
                )

        # If there's a significant negative gap down and current price is rising back towards prior close
        elif gap_pct < -min_gap_bps and gap_pct > -max_gap_bps:
            # Look for lack of continuation: current close is above open, and price is moving towards prior_session_close
            if ctx.bar.close > ctx.bar.open and ctx.bar.close > self.prior_session_close + (gap * 0.5): # Price has retraced at least 50% of the gap
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "overnight_gap_down_reversion",
                        "gap_bps": gap_pct,
                        "prior_session_close": self.prior_session_close,
                        "current_open": ctx.bar.open,
                        "current_close": ctx.bar.close,
                        "entry_hour": current_hour
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt based on position close
        pass