from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcEuMorningGapReversion(Strategy):
    METADATA = {
        "name": "BTC EU Morning Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for 1-hour session, but can be shorter
        "warmup_bars": 24, # Need enough bars for prior day's close
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_bar_close_utc6 = None # Close of the 6 AM UTC bar (end of prior session)
        self.entry_bar_index = -1
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Store the close of the 6 AM UTC bar (end of prior session)
        if current_hour == 6:
            self.last_bar_close_utc6 = ctx.bar.close

        # Trading window: UTC 7-12
        if 7 <= current_hour <= 11:
            if ctx.has_position():
                # If we are in position and it's 11 AM UTC, consider exiting before end of session
                if current_hour == 11 and ctx.bar.timestamp.minute >= 50: # Close near end of session
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "session_end_exit", "current_hour": current_hour})
                return None # Hold position during trading hours

            # Cooldown check
            if ctx.bar_index <= self.entry_bar_index + self.cooldown_bars:
                return None

            # Logic for entering a new position
            if self.last_bar_close_utc6 is not None:
                # Open of the current 7 AM UTC bar (start of new session)
                # We need the 7 AM open, so check if current_hour is 7
                if current_hour == 7:
                    # Check for large gap (e.g., 0.5% or more) from last_bar_close_utc6 to current 7 AM open
                    open_7am = ctx.bar.open
                    gap_pct = ((open_7am - self.last_bar_close_utc6) / self.last_bar_close_utc6) * 100

                    gap_threshold_pct = 0.3 # Example: 0.3% gap
                    min_gap_size_usd = 50.0 # Example: at least $50 gap

                    # Only consider a gap if the current bar's close is NOT continuing the gap
                    # i.e., if price is reverting back towards last_bar_close_utc6
                    if abs(gap_pct) >= gap_threshold_pct and abs(open_7am - self.last_bar_close_utc6) >= min_gap_size_usd:
                        # Long setup: Price opened significantly below prior close, but current close is moving up
                        if gap_pct < -gap_threshold_pct and ctx.bar.close > open_7am:
                            self.entry_bar_index = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.75,
                                metadata={
                                    "reason": "eu_morning_gap_reversion_long",
                                    "gap_pct": round(gap_pct, 2),
                                    "open_7am": open_7am,
                                    "prior_close_utc6": self.last_bar_close_utc6,
                                    "current_close": ctx.bar.close,
                                }
                            )
                        # Short setup: Price opened significantly above prior close, but current close is moving down
                        elif gap_pct > gap_threshold_pct and ctx.bar.close < open_7am:
                            self.entry_bar_index = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.75,
                                metadata={
                                    "reason": "eu_morning_gap_reversion_short",
                                    "gap_pct": round(gap_pct, 2),
                                    "open_7am": open_7am,
                                    "prior_close_utc6": self.last_bar_close_utc6,
                                    "current_close": ctx.bar.close,
                                }
                            )
        
        # Reset last_bar_close_utc6 after the trading window or if we're past 12 UTC to avoid stale data
        if current_hour >= 12 and self.last_bar_close_utc6 is not None:
             self.last_bar_close_utc6 = None

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive logic
        pass