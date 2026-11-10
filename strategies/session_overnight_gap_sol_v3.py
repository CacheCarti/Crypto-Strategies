from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolOvernightGapReversion(Strategy):
    METADATA = {
        "name": "SOL Overnight Gap Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold until US session or stop
        "warmup_bars": 24, # Need at least 24 bars for prior day's close
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -9999
        self.cooldown_bars = 6 # Wait 6 hours after a trade
        self.prior_session_close = None
        self.last_overnight_session_start_bar = -9999
        self.gap_threshold_bps = 30.0 # Minimum gap to consider
        self.continuation_threshold_bps = 10.0 # How much continuation before invalidating gap
        self.max_entry_hour = 6 # Enter only before 6 AM UTC
        self.exit_hour = 12 # Exit before US session (12 PM UTC)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(25) # Need prior day's close
        opens = ctx.opens(25)
        highs = ctx.highs(25)
        lows = ctx.lows(25)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Store prior session close (end of US session, 23:00 UTC)
        # This assumes the 'prior session' ends at 23:00 UTC for the next day's overnight session.
        # We look for the close of the bar that ends at 23:00 UTC.
        if current_hour == 23 and ctx.bar_index > 0: # Store the close of the 23:00 bar
            self.prior_session_close = ctx.bar.close

        # Check for trade cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Logic for exiting existing positions
        if ctx.has_position():
            if current_hour >= self.exit_hour:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_exit_time",
                    "current_hour": current_hour,
                    "exit_hour": self.exit_hour,
                    "price": ctx.bar.close
                })
            # Add a basic structure stop-loss if it moves too far against the gap direction
            pos = ctx.position
            if pos["direction"] == "long":
                if ctx.bar.low < pos["entry_price"] * (1 - self.METADATA["declared_sl_bps"] / 10000.0):
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "structure_stop_loss",
                        "direction": "long",
                        "entry_price": pos["entry_price"],
                        "stop_level": pos["entry_price"] * (1 - self.METADATA["declared_sl_bps"] / 10000.0),
                        "current_low": ctx.bar.low
                    })
            elif pos["direction"] == "short":
                if ctx.bar.high > pos["entry_price"] * (1 + self.METADATA["declared_sl_bps"] / 10000.0):
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "structure_stop_loss",
                        "direction": "short",
                        "entry_price": pos["entry_price"],
                        "stop_level": pos["entry_price"] * (1 + self.METADATA["declared_sl_bps"] / 10000.0),
                        "current_high": ctx.bar.high
                    })
            return None # Hold position if not exiting

        # Entry logic: Overnight window (0-6 UTC) and only once per session
        if current_hour >= 0 and current_hour <= self.max_entry_hour:
            # Ensure we only attempt to trade once per overnight session window
            # We track the start of the overnight session (00:00 UTC)
            if current_hour == 0 and self.last_overnight_session_start_bar != ctx.bar_index:
                self.last_overnight_session_start_bar = ctx.bar_index
                # Reset prior close if it's too old or not set
                if self.prior_session_close is None: # or (ctx.bar_index - self.last_trade_bar > 24 and self.prior_session_close is not None):
                    # Try to infer prior close from 24 bars ago if not set
                    if len(closes) >= 24:
                        self.prior_session_close = closes[-24] # Close from 24 hours ago as a fallback
                    else:
                        return None # Not enough data for fallback

            # If we are in the overnight session and prior_session_close is available
            if self.prior_session_close is not None:
                # Calculate gap from prior session close to current bar open
                gap_pct = (ctx.bar.open - self.prior_session_close) / self.prior_session_close * 10000.0 # in bps

                # Check for gap and lack of continuation
                if gap_pct > self.gap_threshold_bps: # Gap up
                    # Check if price has not continued significantly higher
                    # If current price is much higher than open, it's strong continuation, avoid fading
                    continuation_up_bps = (ctx.bar.close - ctx.bar.open) / ctx.bar.open * 10000.0
                    if continuation_up_bps < self.continuation_threshold_bps:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("short", confidence=0.65, metadata={
                            "reason": "overnight_gap_up_fade",
                            "prior_session_close": self.prior_session_close,
                            "current_open": ctx.bar.open,
                            "gap_bps": round(gap_pct, 2),
                            "continuation_bps": round(continuation_up_bps, 2),
                            "current_hour": current_hour
                        })
                elif gap_pct < -self.gap_threshold_bps: # Gap down
                    # Check if price has not continued significantly lower
                    continuation_down_bps = (ctx.bar.open - ctx.bar.close) / ctx.bar.open * 10000.0
                    if continuation_down_bps < self.continuation_threshold_bps:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("long", confidence=0.65, metadata={
                            "reason": "overnight_gap_down_fade",
                            "prior_session_close": self.prior_session_close,
                            "current_open": ctx.bar.open,
                            "gap_bps": round(gap_pct, 2),
                            "continuation_bps": round(continuation_down_bps, 2),
                            "current_hour": current_hour
                        })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar on close to ensure cooldown is enforced correctly
        self.last_trade_bar = ctx.bar_index
        pass