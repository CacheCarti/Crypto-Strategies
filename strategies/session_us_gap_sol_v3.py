from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolUsSessionGapReversion(Strategy):
    METADATA = {
        "name": "SOLUSDC US Session Gap Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours, to allow for reversion
        "warmup_bars": 48,  # Need enough history for previous day's close and 24h volume
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13  # UTC 13:00
        self.us_session_end_hour = 21    # UTC 21:00 (exit before this hour)
        self.min_gap_pct = 0.005         # Minimum 0.5% gap to consider
        self.max_gap_continuation_pct = 0.002 # Max 0.2% continuation before fading
        self.cooldown_bars = 4           # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade
        self.prior_session_close = None
        self.session_gap_direction = None # "up" or "down"
        self.entry_price_this_session = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # --- Session Start Logic ---
        # If it's the start of the US session (13:00 UTC)
        if current_hour == self.us_session_start_hour:
            # Only process once at the start of the hour
            if ctx.bar.timestamp.minute == 0 and ctx.bar.timestamp.hour != (ctx.closes(2)[-2].timestamp.hour if len(ctx.closes(2)) > 1 else -1):
                # Calculate previous session's close (end of last US session, 20:00 UTC)
                # We need to find the close of the bar ending at 20:00 UTC on the previous day
                # Iterate backwards to find the last bar of the previous US session
                prev_session_close_price = None
                for i in range(2, len(closes)): # Look back in history
                    bar_ts = ctx.closes(i)[-i].timestamp
                    if bar_ts.hour == self.us_session_end_hour - 1: # Bar ending at 20:00 UTC
                        prev_session_close_price = ctx.closes(i)[-i]
                        break

                if prev_session_close_price:
                    self.prior_session_close = prev_session_close_price
                    session_open = ctx.bar.open
                    gap_pct = (session_open - self.prior_session_close) / self.prior_session_close

                    if abs(gap_pct) >= self.min_gap_pct:
                        self.session_gap_direction = "up" if gap_pct > 0 else "down"
                        self.entry_price_this_session = None # Reset for new session
                    else:
                        self.session_gap_direction = None
                        self.entry_price_this_session = None
                else:
                    self.session_gap_direction = None
                    self.prior_session_close = None
                    self.entry_price_this_session = None

        # --- Entry Logic ---
        if not ctx.has_position() and self.session_gap_direction and self.prior_session_close:
            if ctx.bar_index > self.last_trade_bar + self.cooldown_bars:
                # Check if current price has moved against the gap, but not too much into continuation
                current_price = ctx.bar.close
                gap_reversion_threshold_long = self.prior_session_close * (1 + self.max_gap_continuation_pct)
                gap_reversion_threshold_short = self.prior_session_close * (1 - self.max_gap_continuation_pct)

                if self.session_gap_direction == "down": # Gap down, look for long
                    # Price is below prior close, but hasn't continued too far down
                    if current_price > self.prior_session_close and current_price < gap_reversion_threshold_long:
                        # Ensure price is still significantly below the session open (reversion has started)
                        if ctx.bar.open < self.prior_session_close * (1 - self.min_gap_pct): # Ensure it was a real gap down
                            self.last_trade_bar = ctx.bar_index
                            self.entry_price_this_session = current_price
                            return ctx.signal(
                                "long",
                                confidence=0.7,
                                metadata={
                                    "reason": "session_gap_down_reversion_entry",
                                    "prior_session_close": self.prior_session_close,
                                    "session_open": ctx.bar.open,
                                    "current_price": current_price,
                                    "gap_direction": "down",
                                    "gap_pct": (ctx.bar.open - self.prior_session_close) / self.prior_session_close,
                                }
                            )

                elif self.session_gap_direction == "up": # Gap up, look for short
                    # Price is above prior close, but hasn't continued too far up
                    if current_price < self.prior_session_close and current_price > gap_reversion_threshold_short:
                        # Ensure price is still significantly above the session open (reversion has started)
                        if ctx.bar.open > self.prior_session_close * (1 + self.min_gap_pct): # Ensure it was a real gap up
                            self.last_trade_bar = ctx.bar_index
                            self.entry_price_this_session = current_price
                            return ctx.signal(
                                "short",
                                confidence=0.7,
                                metadata={
                                    "reason": "session_gap_up_reversion_entry",
                                    "prior_session_close": self.prior_session_close,
                                    "session_open": ctx.bar.open,
                                    "current_price": current_price,
                                    "gap_direction": "up",
                                    "gap_pct": (ctx.bar.open - self.prior_session_close) / self.prior_session_close,
                                }
                            )
        
        # --- Exit Logic ---
        if ctx.has_position():
            # Exit if approaching the end of the US session
            if current_hour >= self.us_session_end_hour - 1: # Exit during the 20:00 UTC bar
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "session_end_exit",
                        "current_hour": current_hour,
                        "session_end_hour": self.us_session_end_hour,
                        "entry_price": ctx.position["entry_price"],
                        "exit_price": ctx.bar.close,
                        "direction": ctx.position_direction(),
                    }
                )
            
            # Additional structure stop: if price moves past the prior session close + a small buffer
            if ctx.position_direction() == "long" and self.prior_session_close and ctx.bar.close < self.prior_session_close * (1 - self.max_gap_continuation_pct):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "structure_stop_long",
                        "prior_session_close": self.prior_session_close,
                        "current_price": ctx.bar.close,
                        "entry_price": ctx.position["entry_price"],
                    }
                )
            elif ctx.position_direction() == "short" and self.prior_session_close and ctx.bar.close > self.prior_session_close * (1 + self.max_gap_continuation_pct):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "structure_stop_short",
                        "prior_session_close": self.prior_session_close,
                        "current_price": ctx.bar.close,
                        "entry_price": ctx.position["entry_price"],
                    }
                )


        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session specific variables on position close
        self.session_gap_direction = None
        self.entry_price_this_session = None
        # self.prior_session_close will be re-evaluated at the next session start
        pass