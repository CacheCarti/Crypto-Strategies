from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionGapReversion(Strategy):
    METADATA = {
        "name": "US Session Gap Reversion ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 150.0,  # Tight SL for quick reversion
        "declared_tp_bps": 300.0,  # TP for quick profit
        "declared_hold_seconds": 3600,  # Intended avg hold time (1 hour bar)
        "warmup_bars": 20,         # Need enough bars for session data
        "required_features": [],   # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_us_session_close = None
        self.last_us_session_open_hour = -1
        self.last_trade_bar_index = -1
        self.cooldown_bars = 5 # Cooldown period after a trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check if we are in a cooldown period
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # US Session: UTC 13-21
        # Detect end of US session (UTC 21) to store closing price
        if current_hour == 21 and self.last_us_session_open_hour != 21:
            self.last_us_session_close = ctx.bar.close
            self.last_us_session_open_hour = current_hour # Mark that we've processed this 21:00 bar

        # Detect start of next US session (UTC 13)
        if current_hour == 13 and self.last_us_session_open_hour != 13:
            self.last_us_session_open_hour = current_hour # Mark that we've processed this 13:00 bar
            
            if self.last_us_session_close is not None:
                us_session_open = ctx.bar.open
                gap_bps = ((us_session_open - self.last_us_session_close) / self.last_us_session_close) * 10000

                # Define a significant gap threshold (e.g., 20 bps)
                significant_gap_bps = 20.0

                # Check for gap-down reversion
                if gap_bps < -significant_gap_bps:
                    # Gap down, look for long signal back to prior close
                    # Only if current price is still below the prior close and has not recovered much
                    if ctx.bar.close < self.last_us_session_close:
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            take_profit_bps=self.METADATA["declared_tp_bps"], # Use declared TP
                            stop_loss_bps=self.METADATA["declared_sl_bps"],   # Use declared SL
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "us_session_gap_down_reversion",
                                "gap_bps": round(gap_bps, 2),
                                "prior_us_close": round(self.last_us_session_close, 2),
                                "us_open": round(us_session_open, 2),
                                "current_close": round(ctx.bar.close, 2)
                            }
                        )

                # Check for gap-up reversion
                elif gap_bps > significant_gap_bps:
                    # Gap up, look for short signal back to prior close
                    # Only if current price is still above the prior close and has not recovered much
                    if ctx.bar.close > self.last_us_session_close:
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "us_session_gap_up_reversion",
                                "gap_bps": round(gap_bps, 2),
                                "prior_us_close": round(self.last_us_session_close, 2),
                                "us_open": round(us_session_open, 2),
                                "current_close": round(ctx.bar.close, 2)
                            }
                        )
            
            # Reset last_us_session_close after evaluating for a new session
            # This ensures we only use the close from the immediately preceding US session
            self.last_us_session_close = None


        # Exit before end of US session (UTC 21) if position is still open
        # This acts as a time-based stop/exit for positions
        if ctx.has_position() and current_hour == 20: # Exit on the 20:00 UTC bar, before 21:00 close
            if ctx.position_direction() == "long":
                exit_reason = "time_based_exit_us_session_end_long"
            else:
                exit_reason = "time_based_exit_us_session_end_short"
            
            self.last_trade_bar_index = ctx.bar_index # Cooldown after exit
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": exit_reason,
                    "current_hour": current_hour,
                    "current_close": round(ctx.bar.close, 2)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive logic
        pass