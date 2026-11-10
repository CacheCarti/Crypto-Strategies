from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeOpenGapReversion(Strategy):
    METADATA = {
        "name": "EuropeOpenGapReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours max hold within session
        "warmup_bars": 24, # Need prior day's close
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.gap_threshold_pct = 0.25 # Percentage gap to consider
        self.continuation_threshold_pct = 0.1 # How much price moves in direction of gap
        self.max_entry_hour = 10 # Enter up to UTC 10
        self.exit_hour = 12 # Exit by UTC 12
        self.min_cooldown_bars = 4 # Cooldown after a trade
        self.last_trade_bar = -self.min_cooldown_bars

        # Store prior day's close for gap calculation
        self.prior_session_close = None
        self.last_day = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Update prior session close at the end of UTC 23 (or start of UTC 00 for next day's reference)
        # We need the close of the previous day to calculate the gap at the start of the European session.
        # Let's define "prior session close" as the close of the UTC 23 bar.
        if current_hour == 23 and current_day != self.last_day:
            self.prior_session_close = ctx.bar.close
            self.last_day = current_day
            return None # No trading at this hour, just update reference

        # Trading window: UTC 7 to UTC 10 for entry, exit by UTC 12
        if not (7 <= current_hour <= self.exit_hour):
            if ctx.has_position() and current_hour > self.exit_hour:
                # If we are past exit_hour and still in a position, close it
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour, "exit_hour": self.exit_hour})
            return None

        # --- Entry Logic ---
        if ctx.has_position():
            return None # Already in a position

        if current_hour > self.max_entry_hour:
            return None # Past the entry window

        if ctx.bar_index - self.last_trade_bar < self.min_cooldown_bars:
            return None # Cooldown in effect

        if self.prior_session_close is None:
            return None # Need a prior session close reference

        # Check for gap at the start of the European session (e.g., UTC 7)
        # We need the open of the current bar (UTC 7) to compare with prior_session_close
        if current_hour == 7:
            session_open = ctx.bar.open
            gap_pct = ((session_open - self.prior_session_close) / self.prior_session_close) * 100

            # Check if there's a significant gap
            if abs(gap_pct) > self.gap_threshold_pct:
                # Now check for lack of continuation in the first few bars of the session
                # We need to look at the current bar's close relative to its open
                # or a few bars if we are past the 7th hour
                
                # For simplicity, let's check the current bar's movement as a proxy for continuation lack
                # If the gap was up, but price is now moving down (or not moving up much)
                # If the gap was down, but price is now moving up (or not moving down much)
                
                # We need at least two bars to check for continuation effectively.
                # Let's use the close of the current bar (which is the first bar of the session)
                # and compare it to the session_open.
                
                # If we are at the 7th hour, we only have one bar to check.
                # Let's simplify: if the gap is large, we assume a fade.
                # A more robust check would involve looking at the high/low of the first 1-2 bars
                # after the gap. Since we only have the current bar, we use its close.

                # Simplified continuation check: if the price has not moved significantly
                # in the direction of the gap within the current bar.
                
                # Example: gap up, but current bar closes near open or lower.
                # Or gap down, but current bar closes near open or higher.

                # To make this robust, we should check a few bars after the open.
                # Let's consider the current bar (which is the bar at current_hour)
                # and see if it reverses part of the gap.

                # If gap is UP
                if gap_pct > 0:
                    # Price gapped up. We expect it to continue up if it's strong.
                    # If it fails to continue up (e.g., current close is lower than open, or only slightly higher),
                    # we fade the gap (short).
                    # A better check: if current close is near or below the prior_session_close
                    # after a gap up.

                    # Let's define "lack of continuation" as:
                    # For a gap UP: the current close is less than (open + (gap_size * continuation_threshold_pct))
                    # Effectively, it didn't move much further up.
                    # Even simpler: if the price has started to retrace towards the prior_session_close
                    # i.e., current_close < session_open.
                    # This is too strong. Let's say if the bar doesn't extend the gap significantly.
                    # A good proxy for "lack of continuation" and "reversion" is if the current price
                    # is already below the mid-point of the gap, or has started to move against it.

                    # Let's use a simpler rule for now: if the current close is below the session open after a gap up,
                    # or current close is above session open after a gap down.
                    # This is a one-bar reversal setup.

                    if ctx.bar.close < session_open: # Gap up, but current bar is down
                        signal_direction = "short"
                        confidence = 0.7 + ctx.rng.uniform(0, 0.1) # Add a bit of randomness
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            signal_direction,
                            confidence=confidence,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "europe_open_gap_up_reversion",
                                "gap_pct": round(gap_pct, 2),
                                "prior_session_close": self.prior_session_close,
                                "session_open": session_open,
                                "current_close": ctx.bar.close,
                                "entry_hour": current_hour
                            }
                        )
                # If gap is DOWN
                elif gap_pct < 0:
                    # Price gapped down. We expect it to continue down if it's strong.
                    # If it fails to continue down (e.g., current close is higher than open, or only slightly lower),
                    # we fade the gap (long).
                    if ctx.bar.close > session_open: # Gap down, but current bar is up
                        signal_direction = "long"
                        confidence = 0.7 + ctx.rng.uniform(0, 0.1)
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            signal_direction,
                            confidence=confidence,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "europe_open_gap_down_reversion",
                                "gap_pct": round(gap_pct, 2),
                                "prior_session_close": self.prior_session_close,
                                "session_open": session_open,
                                "current_close": ctx.bar.close,
                                "entry_hour": current_hour
                            }
                        )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown or log
        pass # Currently, cooldown is handled by self.last_trade_bar in on_bar