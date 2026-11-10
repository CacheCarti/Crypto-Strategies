from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanOpenGapReversion(Strategy):
    METADATA = {
        "name": "European Open Gap Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # Max hold until 12 UTC, around 2 hours
        "warmup_bars": 30,
        "required_features": [], # No specific features needed beyond price/volume
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.daily_trade_made = False # Flag to ensure only one trade per session
        self.prior_session_close = None # Stores the close of the previous day's European session

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        if len(closes) < self.METADATA["warmup_bars"] + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day_of_week = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Reset daily trade flag at the beginning of a new day (UTC 00:00)
        if current_hour == 0 and not ctx.has_position():
            self.daily_trade_made = False

        # Store prior session close at the end of the European session (UTC 12:00)
        # We assume the prior session ends at 12:00 UTC
        if current_hour == 12 and current_day_of_week < 5: # Only on weekdays
            self.prior_session_close = ctx.bar.close

        # Trading window: European morning (UTC 7-12)
        if not (7 <= current_hour < 12) or current_day_of_week >= 5: # Only on weekdays within window
            # If we are outside the trading window and have a position, close it
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only one trade per session
        if self.daily_trade_made:
            return None

        # Check for prior session close presence
        if self.prior_session_close is None:
            return None

        # Gap detection and continuation check
        open_price = ctx.bar.open
        current_close = ctx.bar.close
        gap_threshold_bps = 50.0 # Define a minimum gap size in bps
        continuation_threshold_bps = 20.0 # How much price should move away from the gap

        # Calculate gap size relative to prior session close
        gap_from_prior_close = (open_price - self.prior_session_close) / self.prior_session_close * 10000

        # Check for significant gap up and lack of continuation
        if gap_from_prior_close > gap_threshold_bps: # Gap up
            # If price opens significantly above prior close but then moves back down towards it
            # Or fails to continue higher
            if current_close < open_price - (open_price * continuation_threshold_bps / 10000):
                # Signal short: price gapped up but failed to hold and is reversing
                self.last_trade_bar = ctx.bar_index
                self.daily_trade_made = True
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "european_open_gap_up_reversion",
                        "prior_session_close": self.prior_session_close,
                        "open_price": open_price,
                        "current_close": current_close,
                        "gap_bps": gap_from_prior_close,
                        "current_hour": current_hour
                    }
                )

        # Check for significant gap down and lack of continuation
        elif gap_from_prior_close < -gap_threshold_bps: # Gap down
            # If price opens significantly below prior close but then moves back up towards it
            # Or fails to continue lower
            if current_close > open_price + (open_price * continuation_threshold_bps / 10000):
                # Signal long: price gapped down but failed to hold and is reversing
                self.last_trade_bar = ctx.bar_index
                self.daily_trade_made = True
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "european_open_gap_down_reversion",
                        "prior_session_close": self.prior_session_close,
                        "open_price": open_price,
                        "current_close": current_close,
                        "gap_bps": gap_from_prior_close,
                        "current_hour": current_hour
                    }
                )

        # If we have a position, but the session is ending soon (e.g., last hour), consider exiting if not profitable
        if ctx.has_position() and current_hour == 11:
            # Optionally add logic to check profitability or position age before exiting
            return ctx.signal("flat", confidence=0.6, metadata={"reason": "session_end_approaching_exit", "current_hour": current_hour})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown and daily trade flag if position closes for any reason
        self.last_trade_bar = ctx.bar_index
        # self.daily_trade_made = False # No, keep it true for the rest of the day to avoid re-entry
        # You could use this to log performance or adapt parameters
        pass