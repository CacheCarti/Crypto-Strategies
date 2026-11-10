from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapReversion(Strategy):
    METADATA = {
        "name": "Session Gap Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4-hour hold
        "warmup_bars": 30,
        "required_features": [], # No specific features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_close_price = None
        self.last_trade_bar = -self.METADATA["warmup_bars"]
        self.cooldown_bars = 5 # Cooldown period after a trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Handle existing position
        if ctx.has_position():
            # Exit before UTC 0 (end of US session) if position is still open
            if current_hour == 23 and ctx.position_direction() is not None: # Exit at 23:59:59
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "end_of_session_exit",
                        "current_price": current_price,
                        "entry_price": ctx.position["entry_price"],
                        "direction": ctx.position["direction"],
                    }
                )
            return None # Hold position

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Logic for late US session (18:00 - 23:00 UTC)
        if 18 <= current_hour < 23:
            # At 18:00, if we don't have a session_close_price from the previous day, try to set it
            # This handles the case where the strategy starts mid-day or misses the 17:00 bar
            if current_hour == 18 and self.session_close_price is None and ctx.bar_index > 0:
                # Look back to the previous bar's hour to determine if it was 17:00
                prev_bar_timestamp = ctx.bar.timestamp - ctx.market.get("bar_duration_seconds", 3600)
                if prev_bar_timestamp.hour == 17:
                    self.session_close_price = ctx.closes(2)[0] # Close of the previous bar (17:00 UTC)

            # Only trade if we have a prior session close price
            if self.session_close_price is None:
                return None

            gap_threshold_bps = 50 # Minimum gap to consider, in bps
            gap_bps = (current_price - self.session_close_price) / self.session_close_price * 10000

            # Check for gap and potential reversion
            if abs(gap_bps) > gap_threshold_bps:
                # If price gapped up significantly and is now showing weakness (reversion)
                if gap_bps > gap_threshold_bps: # Gapped up
                    # Look for continuation failure: current price should be below or struggling at open
                    # Or simply, if price is far from previous close, bet on reversion
                    if current_price < self.session_close_price * (1 + gap_threshold_bps / 20000): # Price hasn't continued strongly
                         # Attempt to short back to previous close
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.65,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "session_gap_up_reversion",
                                "current_price": current_price,
                                "prior_session_close": self.session_close_price,
                                "gap_bps": gap_bps,
                                "entry_hour": current_hour,
                            }
                        )
                # If price gapped down significantly and is now showing strength (reversion)
                elif gap_bps < -gap_threshold_bps: # Gapped down
                    if current_price > self.session_close_price * (1 - gap_threshold_bps / 20000): # Price hasn't continued strongly
                        # Attempt to long back to previous close
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.65,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "session_gap_down_reversion",
                                "current_price": current_price,
                                "prior_session_close": self.session_close_price,
                                "gap_bps": gap_bps,
                                "entry_hour": current_hour,
                            }
                        )
        # End of session (UTC 0:00 - 17:00) - reset prior session close
        elif current_hour == 0:
            # Capture the close of the previous bar (which was 23:00 UTC) as the session close
            if ctx.bar_index > 0:
                self.session_close_price = ctx.closes(2)[0]
            else:
                self.session_close_price = None # Reset if no prior bar
            return None # Do not trade outside of specified session

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for debugging or adaptive logic
        # For this strategy, no specific adaptive logic is implemented here.
        pass