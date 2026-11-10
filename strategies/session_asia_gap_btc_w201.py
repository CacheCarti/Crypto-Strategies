from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionGapReversion(Strategy):
    METADATA = {
        "name": "Asia Session Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2,  # Up to 2 hours, aiming to close before 8 UTC
        "warmup_bars": 24, # Need at least 24 bars to get a full day's data
        "required_features": ["funding_rate_btcusdt"] # Using funding rate as a bias filter
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -1
        self.cooldown_bars = 4 # Wait 4 hours after a trade
        self.prior_asia_close = None
        self.traded_today = False # To ensure only one trade per day/session

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1) # Need one more for prior close
        highs = ctx.highs(self.METADATA["warmup_bars"] + 1)
        lows = ctx.lows(self.METADATA["warmup_bars"] + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Reset for a new day (after 8 UTC)
        if current_hour == 8:
            self.traded_today = False
            # Store the closing price of the Asia session (0-7 UTC)
            if len(closes) >= 8: # Ensure we have at least 8 closes for the Asia session
                 # This assumes the current bar is the 8th bar (0-indexed 7) of the new day
                 # The "prior_asia_close" is the close of the 7 UTC bar (which is the 8th bar of the Asia session)
                self.prior_asia_close = closes[-1] # The bar that just closed at 7:59:59 (i.e. bar.timestamp.hour == 7)
            else:
                self.prior_asia_close = None # Not enough data for a full Asia session close
            return None

        # Logic for entry during the Asia session (UTC 0-7)
        if current_hour >= 0 and current_hour < 8:
            # Check cooldown
            if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
                return None

            if self.traded_today:
                return None

            # Check if we have a prior Asia close to compare against
            if self.prior_asia_close is None:
                return None

            # Calculate the opening gap from prior Asia close
            # The current bar's open is the open of the first bar after 8 UTC
            # But we are trading within the Asia session (0-7 UTC)
            # So, we need to compare the current session's open to the prior session's close
            # We are trying to capture a gap from the previous day's Asia close to the current bar's open
            # This strategy is about the *current* Asia session's price action
            # Let's define the "gap" as the difference between the *start* of the current Asia session (0 UTC) and the prior Asia close.
            # Then, we look for reversion within the current Asia session.

            # Re-evaluating the "gap" logic based on "prior session close to next session open"
            # Since we are in the Asia session (0-7 UTC), the "prior session close" would be yesterday's Asia close (which we store).
            # The "next session open" would be today's Asia open (0 UTC).
            
            # Let's refine: We are looking for a gap from the *previous day's UTC 7 close* (self.prior_asia_close)
            # to the *current day's UTC 0 open*.
            # If the current hour is 0 (the first hour of the Asia session), we can check the gap.
            if current_hour == 0 and self.prior_asia_close is not None:
                current_asia_open = ctx.bar.open # This is the open of the 0 UTC bar
                gap_bps = (current_asia_open - self.prior_asia_close) / self.prior_asia_close * 10000

                # Define a significant gap (e.g., 50 bps)
                significant_gap_threshold = 30.0 # bps

                # Check for gap reversion opportunity
                signal_direction = None
                reason = ""
                confidence = 0.0

                # If there's a large positive gap up, look for a short if it fails to continue higher
                if gap_bps > significant_gap_threshold:
                    # Look for price to fail to hold the open or show weakness
                    # For simplicity, if current price is below the open of the first Asia bar
                    if current_close < current_asia_open:
                        signal_direction = "short"
                        reason = "asia_gap_up_reversion"
                        confidence = min(0.8, abs(gap_bps) / 100.0) # Scale confidence with gap size
                # If there's a large negative gap down, look for a long if it fails to continue lower
                elif gap_bps < -significant_gap_threshold:
                    # Look for price to fail to hold the open or show strength
                    if current_close > current_asia_open:
                        signal_direction = "long"
                        reason = "asia_gap_down_reversion"
                        confidence = min(0.8, abs(gap_bps) / 100.0) # Scale confidence with gap size

                if signal_direction:
                    # Add a filter: only trade if funding rate is not extremely against us
                    funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
                    if (signal_direction == "long" and funding_rate > 0.0001) or \
                       (signal_direction == "short" and funding_rate < -0.0001):
                        return None # Funding too expensive for this direction

                    self.last_trade_bar = ctx.bar_index
                    self.traded_today = True
                    return ctx.signal(
                        signal_direction,
                        confidence=confidence,
                        metadata={
                            "reason": reason,
                            "gap_bps": gap_bps,
                            "prior_asia_close": self.prior_asia_close,
                            "current_asia_open": current_asia_open,
                            "current_close": current_close,
                            "funding_rate": funding_rate
                        }
                    )

        # If a position is open and it's approaching the end of the Asia session (e.g., 7 UTC), consider exiting
        # This acts as a time-based stop/take-profit, fulfilling "exit before UTC 8"
        if ctx.has_position() and ctx.position_direction() is not None:
            if current_hour == 7: # Last hour of Asia session
                # If we're in profit, take it. Otherwise, flatten to avoid holding through session change.
                # Use a small confidence to indicate a time-based exit rather than strong conviction
                return ctx.signal(
                    "flat",
                    confidence=0.1,
                    metadata={"reason": "end_of_asia_session_exit", "current_hour": current_hour}
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust parameters
        pass