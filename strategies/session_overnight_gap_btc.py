from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BTCOvernightGapReversion(Strategy):
    METADATA = {
        "name": "BTC Overnight Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended max hold of 4 hours
        "warmup_bars": 24, # Need enough bars for prior day's close and overnight candle
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.prior_session_close = None
        self.prior_session_close_bar_index = -1
        self.gap_direction = None # "up_gap" or "down_gap"
        self.entry_price_for_gap = None
        self.last_gap_check_day = -1 # To ensure we only check for a gap once per day

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # --- Gap Detection (UTC 0-6 window) ---
        # Define the end of the "prior session" as UTC 0:00 (start of new day)
        # The overnight window is UTC 0:00 to 6:00
        # We look for a gap at the start of the overnight session (UTC 0:00)
        # This logic ensures we only detect a gap once per day at the 0:00 UTC bar.
        if current_hour == 0 and current_day != self.last_gap_check_day:
            # We need the close of the previous day (bar at 23:00 UTC yesterday)
            # and the open of the current day (bar at 0:00 UTC today)
            # To get the 23:00 UTC bar, we need to look back 1 bar from the 0:00 UTC bar
            # (since ctx.closes(n) gives oldest first, ctx.closes(-2) is 2nd to last)
            if len(closes) >= 2:
                prev_day_close = closes[-2] # Close of the 23:00 UTC bar yesterday
                current_day_open = ctx.bar.open # Open of the 0:00 UTC bar today

                gap_threshold_bps = 15.0 # Minimum gap size in bps
                gap = current_day_open - prev_day_close
                gap_bps = (gap / prev_day_close) * 10000

                self.prior_session_close = prev_day_close
                self.prior_session_close_bar_index = ctx.bar_index - 1 # This is the index of prev_day_close

                self.gap_direction = None
                self.entry_price_for_gap = None

                if gap_bps > gap_threshold_bps:
                    self.gap_direction = "up_gap"
                elif gap_bps < -gap_threshold_bps:
                    self.gap_direction = "down_gap"
                
                self.last_gap_check_day = current_day # Mark that we've processed this day's gap

        # --- Trading Logic (only within the overnight window and if a gap exists) ---
        # Trade between UTC 0:00 and 6:00 (inclusive)
        if 0 <= current_hour <= 6 and self.gap_direction is not None and not ctx.has_position():
            if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
                return None # Cooldown active

            current_price = ctx.bar.close

            # Reversion from an up gap (price gapped up, we expect it to revert down)
            if self.gap_direction == "up_gap":
                # Only enter if price shows some reversion by falling from the open
                # and is still above the prior session close but showing weakness
                if current_price < ctx.bar.open and current_price > self.prior_session_close:
                    # Check for lack of continuation: price tried to go higher but failed
                    # This is a very simple check, could be improved with high/low
                    if ctx.bar.high == ctx.bar.open and ctx.bar.close < ctx.bar.open: # Open = High, then fell
                        self.last_trade_bar = ctx.bar_index
                        self.entry_price_for_gap = current_price
                        return ctx.signal(
                            "short",
                            confidence=0.65,
                            metadata={
                                "reason": "overnight_up_gap_reversion_short",
                                "gap_direction": "up",
                                "gap_bps": (ctx.bar.open - self.prior_session_close) / self.prior_session_close * 10000,
                                "prior_session_close": self.prior_session_close,
                                "current_price": current_price,
                                "current_hour": current_hour,
                            }
                        )

            # Reversion from a down gap (price gapped down, we expect it to revert up)
            elif self.gap_direction == "down_gap":
                # Only enter if price shows some reversion by rising from the open
                # and is still below the prior session close but showing strength
                if current_price > ctx.bar.open and current_price < self.prior_session_close:
                    # Check for lack of continuation: price tried to go lower but failed
                    # This is a very simple check
                    if ctx.bar.low == ctx.bar.open and ctx.bar.close > ctx.bar.open: # Open = Low, then rose
                        self.last_trade_bar = ctx.bar_index
                        self.entry_price_for_gap = current_price
                        return ctx.signal(
                            "long",
                            confidence=0.65,
                            metadata={
                                "reason": "overnight_down_gap_reversion_long",
                                "gap_direction": "down",
                                "gap_bps": (ctx.bar.open - self.prior_session_close) / self.prior_session_close * 10000,
                                "prior_session_close": self.prior_session_close,
                                "current_price": current_price,
                                "current_hour": current_hour,
                            }
                        )

        # --- Exit Logic ---
        # Exit any open position before the next major session (e.g., UTC 7:00)
        # or if it has reverted close to the prior session close
        if ctx.has_position():
            # Force exit before the overnight session ends (e.g. at 7:00 UTC)
            if current_hour >= 7: # Or slightly before, e.g., current_hour == 6 and ctx.bar.timestamp.minute >= 50
                self.last_trade_bar = ctx.bar_index # Cooldown
                self.gap_direction = None # Reset gap state for the day
                self.prior_session_close = None
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "time_based_exit_overnight_session_end", "current_hour": current_hour})

            # Exit if price has reverted significantly towards the prior session close
            # This acts as a soft take-profit/structure stop
            if self.gap_direction == "up_gap" and ctx.position_direction() == "short":
                if ctx.bar.close <= self.prior_session_close * (1 + 0.0005): # Reverted to within 5 bps of prior close
                    self.last_trade_bar = ctx.bar_index
                    self.gap_direction = None
                    self.prior_session_close = None
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "reverted_to_prior_close_up_gap_exit", "current_price": ctx.bar.close, "prior_close": self.prior_session_close})
            elif self.gap_direction == "down_gap" and ctx.position_direction() == "long":
                if ctx.bar.close >= self.prior_session_close * (1 - 0.0005): # Reverted to within 5 bps of prior close
                    self.last_trade_bar = ctx.bar_index
                    self.gap_direction = None
                    self.prior_session_close = None
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "reverted_to_prior_close_down_gap_exit", "current_price": ctx.bar.close, "prior_close": self.prior_session_close})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset gap state after position close, whether it was SL, TP, or flat
        self.gap_direction = None
        self.prior_session_close = None
        self.entry_price_for_gap = None
        self.last_trade_bar = ctx.bar_index # Ensure cooldown after any close