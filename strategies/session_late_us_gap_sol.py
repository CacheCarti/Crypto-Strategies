from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionGapReversion(Strategy):
    METADATA = {
        "name": "SOL Session Gap Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours
        "warmup_bars": 24, # Need at least 24 bars for prior day's close
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -1
        self.cooldown_bars = 6 # Wait 6 hours after a trade
        self.session_gap_threshold_pct = 0.5 # 0.5% gap
        self.continuation_threshold_pct = 0.2 # 0.2% move in the same direction

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(25) # Need yesterday's close and today's open
        opens = ctx.opens(25)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Only consider trading in the late US session (UTC 18-24)
        if not (18 <= current_hour < 24):
            return None

        # Cooldown period
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Exit position before UTC 0 if still open, or on structure stop
        if ctx.has_position() and current_hour == 23: # Exit at the end of the session
            if ctx.position_direction() == "long":
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "session_end_exit", "current_hour": current_hour, "entry_price": ctx.position["entry_price"]})
            elif ctx.position_direction() == "short":
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "session_end_exit", "current_hour": current_hour, "entry_price": ctx.position["entry_price"]})

        # Logic for first bar of the session (UTC 18)
        if current_hour == 18:
            # Find the close of the prior session (yesterday's UTC 23 close)
            # This requires looking back 19-20 bars from current UTC 18 to find yesterday's UTC 23
            # If current bar is UTC 18, then UTC 23 is 19 bars ago (18-19 = -1, which is 23 of previous day)
            # This is tricky with fixed array indices, safer to iterate backwards
            
            prior_session_close = None
            # Find the close price from the bar that was UTC 23 of the previous day
            # Assuming 1h bars, we need to find the bar with timestamp.hour == 23 among previous bars
            
            # Look back through recent bars to find the close of previous day's 23rd hour (UTC)
            # This is a bit of a hack without full timestamp history, assuming typical daily cycle
            # For 18:00 today, yesterday's 23:00 close is 19 bars ago.
            if len(closes) < 20: # Need at least 20 bars to get yesterday's 23:00 close
                return None
            prior_session_close = closes[-20] # close at T-19 hours (which is 23:00 yesterday if current is 18:00)

            if prior_session_close is None:
                return None

            current_open = ctx.bar.open
            gap_pct = ((current_open - prior_session_close) / prior_session_close) * 100

            # Check for a significant gap
            if abs(gap_pct) > self.session_gap_threshold_pct:
                # Check for lack of continuation in the first hour of the session (18:00 bar)
                # If gap was up, but price moved down (or barely up) in the first hour
                # If gap was down, but price moved up (or barely down) in the first hour
                
                # Continuation is measured from session open to current close
                continuation_move_pct = ((ctx.bar.close - current_open) / current_open) * 100

                if gap_pct > 0 and continuation_move_pct < self.continuation_threshold_pct: # Up gap, no strong upward continuation
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("short", confidence=0.7,
                                          metadata={"reason": "gap_up_reversion_no_continuation",
                                                    "gap_pct": round(gap_pct, 2),
                                                    "continuation_pct": round(continuation_move_pct, 2),
                                                    "prior_close": round(prior_session_close, 2),
                                                    "current_open": round(current_open, 2)})
                elif gap_pct < 0 and continuation_move_pct > -self.continuation_threshold_pct: # Down gap, no strong downward continuation
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("long", confidence=0.7,
                                          metadata={"reason": "gap_down_reversion_no_continuation",
                                                    "gap_pct": round(gap_pct, 2),
                                                    "continuation_pct": round(continuation_move_pct, 2),
                                                    "prior_close": round(prior_session_close, 2),
                                                    "current_open": round(current_open, 2)})
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this simple strategy, but good to have the hook
        pass