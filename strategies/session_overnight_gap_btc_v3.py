from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcOvernightGapReversion(Strategy):
    METADATA = {
        "name": "BTC Overnight Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended avg hold time is ~4 hours
        "warmup_bars": 24, # Need at least 24 bars to get prior day's close and current open
        "required_features": [], # No specific features needed, using OHLCV and timestamp
    }

    def initialize(self, ctx: BarContext) -> None:
        self.prior_utc_0_close = None
        self.last_signal_bar_index = -self.METADATA["warmup_bars"] - 1 # Initialize to allow immediate trading after warmup
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check if we are in the overnight window (UTC 0-6)
        # and if it's the start of this window (UTC 0 hour)
        if current_hour == 0:
            # At UTC 0, the prior bar (index -1) was the close of the previous day
            # We need to make sure we have enough data for the prior day's close
            if len(closes) >= 24: # Ensure we have at least a full day of data
                self.prior_utc_0_close = closes[-2] # Close of the previous day (bar at 23:00 UTC)
            else:
                self.prior_utc_0_close = None
            return None # Don't trade at 0 UTC, just capture the previous day's close

        # Trading window: UTC 1 to 6 (inclusive)
        if 1 <= current_hour <= 6 and self.prior_utc_0_close is not None:
            # Only allow one entry per session (daily 0-6 UTC window)
            if ctx.bar_index <= self.last_signal_bar_index + self.cooldown_bars:
                return None

            # Calculate the open of the current session (at UTC 0)
            # This would be the close of the bar at 0 UTC, which is opens[-1] if we are at hour 1
            # or opens[-current_hour] if we are further into the session.
            # Let's use the bar that opened at UTC 0.
            # We need to look back to find the open price of the bar that started at 0 UTC
            # The current bar is ctx.bar.open, previous bar was closes[-1]
            # The open of the bar at 0 UTC would be opens[-current_hour]
            session_open_price = opens[-current_hour] # The open price of the bar that started at 0 UTC

            # Calculate the gap as a percentage
            gap_bps = ((session_open_price - self.prior_utc_0_close) / self.prior_utc_0_close) * 10000

            # Define a significant gap threshold (e.g., 20 bps)
            significant_gap_bps = 20.0

            # Only trade if there's no open position
            if not ctx.has_position():
                if gap_bps > significant_gap_bps:  # Large upward gap
                    # If the current price has not continued higher, and is moving back towards prior close
                    # We look for reversion to the mean (prior close)
                    # Current price is below the session open, suggesting weakness
                    if ctx.bar.close < session_open_price:
                        # Enter short, expecting a reversion towards prior_utc_0_close
                        confidence = min(0.8, abs(gap_bps) / (significant_gap_bps * 2)) # Confidence scales with gap size
                        self.last_signal_bar_index = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=confidence,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "overnight_gap_reversion_short",
                                "prior_utc_0_close": self.prior_utc_0_close,
                                "session_open_price": session_open_price,
                                "current_close": ctx.bar.close,
                                "gap_bps": round(gap_bps, 2),
                                "current_hour": current_hour
                            }
                        )
                elif gap_bps < -significant_gap_bps:  # Large downward gap
                    # If the current price has not continued lower, and is moving back towards prior close
                    # Current price is above the session open, suggesting strength
                    if ctx.bar.close > session_open_price:
                        # Enter long, expecting a reversion towards prior_utc_0_close
                        confidence = min(0.8, abs(gap_bps) / (significant_gap_bps * 2)) # Confidence scales with gap size
                        self.last_signal_bar_index = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "overnight_gap_reversion_long",
                                "prior_utc_0_close": self.prior_utc_0_close,
                                "session_open_price": session_open_price,
                                "current_close": ctx.bar.close,
                                "gap_bps": round(gap_bps, 2),
                                "current_hour": current_hour
                            }
                        )

        # Exit logic: If we are holding a position and it's approaching the end of our trading window (UTC 7)
        # or if it's past the window and we should have exited.
        if ctx.has_position() and current_hour >= 7:
            # Exit before the next US session or end of our analysis window
            return ctx.signal(
                "flat",
                confidence=0.7, # High confidence for scheduled exit
                metadata={
                    "reason": "session_end_exit",
                    "current_hour": current_hour,
                    "position_direction": ctx.position_direction(),
                    "entry_price": ctx.position["entry_price"]
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We can log position details here if needed, or adapt sizing.
        # For this strategy, simply reset any daily state if necessary, but
        # the last_signal_bar_index cooldown handles most of it.
        pass