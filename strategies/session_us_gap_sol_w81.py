from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionGapReversion(Strategy):
    METADATA = {
        "name": "SOL Session Gap Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Approx 1 hour bar hold
        "warmup_bars": 30,
        "required_features": [], # No specific external features needed for this core logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_us_session_close = None
        self.last_signal_bar_index = -self.METADATA["warmup_bars"] # Initialize to allow immediate trading after warmup
        self.cooldown_bars = 5 # Wait 5 bars after a trade before entering again
        self.session_start_hour = 13 # UTC 13 (US session starts)
        self.session_end_hour = 21 # UTC 21 (US session ends)
        self.min_gap_bps = 50.0 # Minimum gap to consider for a trade (0.5%)
        self.reversion_threshold_bps = 25.0 # How much price needs to move back for a signal (0.25%)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        opens = ctx.opens(self.METADATA["warmup_bars"] + 1)
        
        if len(closes) < 2: # Need at least current and previous bar to check for session boundary
            return None

        current_hour = ctx.bar.timestamp.hour
        prev_hour = ctx.bar.timestamp.hour if ctx.bar_index == 0 else ctx.closes(2)[0].hour # Get prev bar's hour

        # Check if we are in a position
        has_position = ctx.has_position()

        # Logic for exiting before session end
        if has_position and ctx.position_direction() == "long" and current_hour >= self.session_end_hour:
            # If we're long and it's past session end, close
            return ctx.signal("flat", confidence=0.7, metadata={
                "reason": "exit_us_session_end",
                "current_hour": current_hour,
                "entry_price": ctx.position["entry_price"],
                "current_price": ctx.bar.close
            })
        elif has_position and ctx.position_direction() == "short" and current_hour >= self.session_end_hour:
            # If we're short and it's past session end, close
            return ctx.signal("flat", confidence=0.7, metadata={
                "reason": "exit_us_session_end",
                "current_hour": current_hour,
                "entry_price": ctx.position["entry_price"],
                "current_price": ctx.bar.close
            })


        # Logic for identifying session close
        if prev_hour < self.session_end_hour and current_hour >= self.session_end_hour:
            # The previous bar was the last bar of the US session
            self.last_us_session_close = closes[-2] # Close of the previous bar (last bar of US session)
            # Reset last signal bar index to allow trading in the new session
            self.last_signal_bar_index = -self.cooldown_bars # Allow trade next session

        # Entry logic: Only trade during the US session (13-21 UTC) and if not in cooldown
        if not has_position and \
           current_hour >= self.session_start_hour and \
           current_hour < self.session_end_hour and \
           ctx.bar_index >= (self.last_signal_bar_index + self.cooldown_bars):

            if self.last_us_session_close is not None:
                session_open = ctx.bar.open
                gap_pct = ((session_open - self.last_us_session_close) / self.last_us_session_close) * 100

                # Long signal: Significant negative gap and price shows reversion
                if gap_pct < -self.min_gap_bps / 100.0:
                    # Price opened significantly lower than previous US session close
                    # Look for reversion: current price moved up from session open
                    reversion_up_bps = ((ctx.bar.close - session_open) / session_open) * 10000
                    if reversion_up_bps >= self.reversion_threshold_bps:
                        self.last_signal_bar_index = ctx.bar_index
                        return ctx.signal("long", confidence=0.8, metadata={
                            "reason": "us_session_gap_reversion_long",
                            "gap_pct": gap_pct,
                            "reversion_up_bps": reversion_up_bps,
                            "session_open": session_open,
                            "last_us_session_close": self.last_us_session_close,
                            "current_price": ctx.bar.close
                        })

                # Short signal: Significant positive gap and price shows reversion
                elif gap_pct > self.min_gap_bps / 100.0:
                    # Price opened significantly higher than previous US session close
                    # Look for reversion: current price moved down from session open
                    reversion_down_bps = ((session_open - ctx.bar.close) / session_open) * 10000
                    if reversion_down_bps >= self.reversion_threshold_bps:
                        self.last_signal_bar_index = ctx.bar_index
                        return ctx.signal("short", confidence=0.8, metadata={
                            "reason": "us_session_gap_reversion_short",
                            "gap_pct": gap_pct,
                            "reversion_down_bps": reversion_down_bps,
                            "session_open": session_open,
                            "last_us_session_close": self.last_us_session_close,
                            "current_price": ctx.bar.close
                        })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last signal bar index to allow trading after cooldown
        self.last_signal_bar_index = ctx.bar_index