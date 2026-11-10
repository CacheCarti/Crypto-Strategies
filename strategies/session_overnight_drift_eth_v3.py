from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthOvernightDrift(Strategy):
    METADATA = {
        "name": "ETH Overnight Drift",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Target 4-hour hold, exit before next session
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this price-action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24 # For median range calculation
        self.min_session_move_factor = 1.0 # Multiplier for median range
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_signal_bar = -self.cooldown_bars
        self.overnight_start_hour = 0  # UTC 0
        self.overnight_end_hour = 6    # UTC 6 (exclusive)
        self.entry_hour_limit = 7      # Can enter up to 7 UTC
        self.exit_hour_start = 12      # Start exiting around 12 UTC for next session
        self.min_move_threshold_pct = 0.003 # Minimum 0.3% move
        self.max_single_bar_spike_pct = 0.005 # Max 0.5% for a single bar within the session

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        sorted_ranges = sorted(ranges)
        mid = len(sorted_ranges) // 2
        return (sorted_ranges[mid] + sorted_ranges[~mid]) / 2

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + self.warmup_bars)
        opens = ctx.opens(self.lookback_period + self.warmup_bars)
        highs = ctx.highs(self.lookback_period + self.warmup_bars)
        lows = ctx.lows(self.lookback_period + self.warmup_period)

        if len(closes) < self.METADATA["warmup_bars"] + self.lookback_period:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic first
        if ctx.has_position():
            # Exit if we are approaching the next session and haven't hit TP/SL
            if current_hour >= self.exit_hour_start and ctx.position_direction() is not None:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "time_based_exit_before_us_session",
                    "current_hour": current_hour,
                    "entry_price": ctx.position["entry_price"],
                    "current_price": ctx.bar.close
                })
            # No other exit logic here; rely on declared SL/TP or time-based exit

        # Cooldown check
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        # Entry logic: Only consider entering in the allowed window and if not already in a position
        if not ctx.has_position() and self.overnight_start_hour <= current_hour < self.entry_hour_limit:
            # Check for a new overnight session start (transition)
            # This prevents re-evaluating the same session's move multiple times
            # We want to trigger only once at the beginning of a decisive move or shortly after
            prev_hour = (current_hour - 1 + 24) % 24
            is_new_session_bar = (prev_hour == self.overnight_end_hour - 1) or \
                                 (current_hour == self.overnight_start_hour and prev_hour != self.overnight_start_hour)

            # To avoid trading every bar, we only evaluate the overnight move once
            # This specific strategy evaluates the *previous* overnight session's move
            # from its start up to the current bar.
            # Let's define the overnight session as 0-6 UTC (7 bars: 0,1,2,3,4,5,6)
            # We want to capture the move that happened *within* that period,
            # and then trade on the *continuation* of that move.

            # Find the bars corresponding to the overnight session ending *before* the current bar
            # For simplicity, let's look at the move from 0 UTC to 6 UTC.
            # If current_hour is 0, we can't evaluate the *previous* overnight session yet.
            # If current_hour is 7, we can evaluate the 0-6 UTC session that just finished.
            # This means we might be entering slightly after the session.

            # Look back to the start of the overnight session (UTC 0)
            # For example, if current_hour is 7, we want bars from 0 to 6.
            # The indices are reversed (ctx.closes(N) gives latest at N-1, oldest at 0)
            # So we need to find the bar that corresponds to UTC 0.

            # Simple approach: Check the move from the previous 0 UTC to the current bar
            # if current_hour is 0, the previous day's overnight session ended at 6 UTC.
            # We are looking for a decisive move from 0 UTC to 6 UTC.
            # Let's re-align: the decision to trade for the *current* day is made
            # based on the overnight move that *just completed* (i.e., if current_hour is 7-8).

            # Let's simplify: we are looking for a strong move *during* the overnight hours (0-6 UTC)
            # and then enter to follow that trend.
            # The entry window is 0-7 UTC.
            # If current_hour is 0-6, we are IN the overnight session.
            # If current_hour is 7, we are evaluating the move from 0-6.

            # This strategy will trigger if the *previous* hour was 6 (end of overnight)
            # and the current hour is 7 (start of follow-through window).
            # This ensures we only evaluate the session move once.
            if current_hour == self.overnight_end_hour + 1: # i.e., 7 AM UTC
                # Find the open of the 0 UTC bar and the close of the 6 UTC bar
                # This requires finding historical bar timestamps, which isn't directly available for
                # specific past bars by hour, only by index.
                # A simpler proxy: calculate the move of the last few bars that *would* represent
                # the overnight session.

                # Let's say overnight session is 7 bars (0,1,2,3,4,5,6 UTC)
                # We need the close of the 6th UTC bar and the open of the 0th UTC bar.
                # If current bar is 7 UTC, then closes[-1] is 7 UTC. closes[-2] is 6 UTC. closes[-8] is 0 UTC.
                # This means we need at least 8 bars of data to calculate the overnight move.

                session_bars_needed = self.overnight_end_hour - self.overnight_start_hour + 1
                total_bars_for_move = session_bars_needed + 1 # open of first bar to close of last bar
                
                if len(closes) < total_bars_for_move:
                    return None

                # Calculate the move from the open of the `overnight_start_hour` bar
                # to the close of the `overnight_end_hour` bar.
                # opens[-session_bars_needed - 1] is the open of the 0 UTC bar (if current is 7 UTC)
                # closes[-2] is the close of the 6 UTC bar (if current is 7 UTC)
                
                # Check previous bars for overnight session (0-6 UTC)
                # Assuming current bar is 7 UTC, so closes[-1] is 7 UTC.
                # opens[-(session_bars_needed+1)] gives the open of the 0 UTC bar
                # closes[-2] gives the close of the 6 UTC bar
                
                # Get the closes for the last `session_bars_needed + 1` bars (includes the open of the first)
                relevant_closes = closes[-(session_bars_needed + 1):]
                relevant_opens = opens[-(session_bars_needed + 1):]
                relevant_highs = highs[-(session_bars_needed + 1):]
                relevant_lows = lows[-(session_bars_needed + 1):]
                
                overnight_open_price = relevant_opens[0]
                overnight_close_price = relevant_closes[-2] # Close of the 6 UTC bar

                session_move = overnight_close_price - overnight_open_price
                session_move_pct = session_move / overnight_open_price

                # Calculate median true range for the last `lookback_period` bars
                median_tr = self._median_range(highs, lows, self.lookback_period)
                if median_tr is None:
                    return None

                # Calculate the range of the overnight session
                overnight_session_high = max(relevant_highs[:-1]) # Exclude current bar
                overnight_session_low = min(relevant_lows[:-1]) # Exclude current bar
                overnight_session_range = overnight_session_high - overnight_session_low

                # Check for single bar spike within the session
                # If any single bar's range within the session is too large compared to the whole session move
                is_spike = False
                for i in range(session_bars_needed):
                    bar_range = relevant_highs[i] - relevant_lows[i]
                    if bar_range / overnight_open_price > self.max_single_bar_spike_pct:
                        is_spike = True
                        break

                # Conditions:
                # 1. Decisive move: session_move_pct exceeds min_move_threshold_pct
                # 2. Not exhausted: overnight session range is not excessively large compared to median TR (e.g. < 3 * median_tr)
                # 3. No single-bar spike within the session
                # 4. Move size relative to median TR
                
                # Minimum move in absolute terms
                min_abs_move = self.min_session_move_factor * median_tr
                
                # Check if the session move is decisive and not a spike
                if abs(session_move) > min_abs_move and not is_spike:
                    confidence = 0.6 + min(0.4, abs(session_move) / (2 * min_abs_move)) # Scale confidence by strength of move

                    if session_move > 0: # Bullish overnight drift
                        self.last_signal_bar = ctx.bar_index
                        return ctx.signal("long", confidence=confidence, metadata={
                            "reason": "overnight_drift_continuation_long",
                            "overnight_open": overnight_open_price,
                            "overnight_close": overnight_close_price,
                            "session_move_pct": session_move_pct,
                            "median_tr": median_tr,
                            "overnight_session_range": overnight_session_range,
                            "current_hour": current_hour,
                            "current_price": ctx.bar.close
                        })
                    elif session_move < 0: # Bearish overnight drift
                        self.last_signal_bar = ctx.bar_index
                        return ctx.signal("short", confidence=confidence, metadata={
                            "reason": "overnight_drift_continuation_short",
                            "overnight_open": overnight_open_price,
                            "overnight_close": overnight_close_price,
                            "session_move_pct": session_move_pct,
                            "median_tr": median_tr,
                            "overnight_session_range": overnight_session_range,
                            "current_hour": current_hour,
                            "current_price": ctx.bar.close
                        })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt parameters
        pass