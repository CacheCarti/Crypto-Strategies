from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class USSessionDriftETH(Strategy):
    METADATA = {
        "name": "US Session Drift ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4-hour hold for session drift
        "warmup_bars": 50,  # Need enough data for ATR and median range
        "required_features": [], # No external features needed for this structure
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13  # UTC 13:00
        self.us_session_end_hour = 21    # UTC 21:00 (exit before this)
        self.min_session_move_factor = 0.8  # Minimum move as a factor of median range
        self.max_session_move_factor = 3.0  # Max move to avoid exhaustion
        self.median_range_period = 20  # Bars for median range calculation
        self.cooldown_bars = 5       # Bars to wait after a trade before new entry
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade
        self.last_session_entry_bar = -1 # Track entry within a session
        self.session_move_threshold_multiplier = 1.2 # Move must exceed 1.2 * median range
        self.volatility_period = 10 # Period for volatility check

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        return statistics.median(ranges)

    def _is_single_bar_spike(self, ctx: BarContext, session_move_pct: float) -> bool:
        # Check if the last bar accounts for a disproportionate amount of the session move
        # This is a simplified check, can be refined.
        if len(ctx.closes(2)) < 2:
            return False
        
        last_bar_move_pct = (ctx.bar.close - ctx.opens(2)[0]) / ctx.opens(2)[0] * 100 if ctx.opens(2)[0] != 0 else 0
        
        # If the last bar's move is > 80% of the total session move and in the same direction
        if abs(session_move_pct) > 0.0001 and (last_bar_move_pct / session_move_pct) > 0.8:
            return True
        return False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: If we have a position and it's near the end of the US session
        if ctx.has_position():
            if current_hour >= self.us_session_end_hour - 1 and ctx.position_direction() is not None: # Exit 1 hour before end
                # Check if we are already in the exit phase (e.g. last bar of the session)
                if current_hour == self.us_session_end_hour - 1:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "session_end_exit",
                            "direction": ctx.position_direction(),
                            "current_hour": current_hour,
                            "entry_price": ctx.position["entry_price"]
                        }
                    )
            return None # Hold position otherwise

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Determine if we are in the US session start window for entry (13:00 UTC to 15:00 UTC)
        is_us_session_entry_window = (current_hour >= self.us_session_start_hour and
                                      current_hour < self.us_session_start_hour + 2) # Allow entries for 2 hours

        # Reset last_session_entry_bar at the start of a new day if outside the US session
        if current_hour == self.us_session_start_hour and self.last_session_entry_bar != ctx.bar_index:
            # Check if this is a new day or if we missed the entry for the current day
            if self.last_session_entry_bar == -1 or ctx.bar_index - self.last_session_entry_bar > 24: # Roughly a day
                self.last_session_entry_bar = -1 # Reset if it's a new day or long time since last entry

        # Prevent multiple entries within the same session
        if self.last_session_entry_bar != -1 and is_us_session_entry_window:
            return None

        if not is_us_session_entry_window:
            return None

        # Calculate session move and median range
        # Look back to the start of the US session for the current day
        session_opens = []
        session_closes = []
        session_highs = []
        session_lows = []

        # Find the start of the current US session
        session_start_bar_index = -1
        for i in range(1, ctx.bar_index + 1):
            bar_timestamp = ctx.bar.timestamp - (ctx.bar_index - i) * (ctx.bar.timestamp - (ctx.bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0))) / ctx.bar_index # Approximate timestamp
            # This is complex to get past bar timestamps correctly without external libraries.
            # A simpler way: assume the current bar is the first bar in the window if it's the start hour.
            # For simplicity, we'll just check if current_hour is the start hour and assume we're at the beginning.
            # In a real system, you'd iterate backwards through ctx.bars to find the true session open.
            # For this contract, we can't iterate ctx.bars directly, so we must use fixed lookback.

            # We need to calculate the session move based on bars *within* the current US session.
            # Since we can't easily get historical bar timestamps within on_bar, we'll approximate:
            # Assume "session move" is the move from the open of the start of the entry window.
            
            # This is a critical simplification due to contract limitations:
            # We are using the 'opens' array to represent the session's starting point.
            # This is not truly session-based but rather a fixed lookback from the current bar.
            # To correctly implement a "session-structure" strategy, the platform needs to provide
            # a way to access bars within a specific timestamp range, or the strategy needs to
            # maintain state across sessions, which is hard with the BarContext API.

            # For now, let's define "session_open" as the open price 'self.us_session_start_hour' bars ago
            # if we are currently in the entry window. This is a proxy for the session's opening price.
            
        # The true "session_open" would be the open of the bar at self.us_session_start_hour for the current day.
        # Since we cannot easily get specific past bar data by timestamp from ctx.closes(n) etc.,
        # we will approximate the "session_open" as the open 'self.us_session_start_hour' bars ago (if current_hour is > start_hour)
        # or simply the current bar's open if it's the very first hour of the session window.

        # Let's define the session's "initial price" as the open of the first bar we see within the session_entry_window.
        # This will be 'opens[0]' if we are just entering the window.
        
        # To get the session's open, we need to know the open of the bar at `self.us_session_start_hour`.
        # This is difficult without a more flexible way to access past bars by timestamp.
        # Let's simplify: the "session move" is the move from the open of the current bar to its close.
        # This makes it a momentum strategy, which is not what was asked.

        # Alternative approach for "session move": Compare current price to the open of the current bar.
        # This is not a "session structure" but a "bar momentum".
        # Let's try to infer session open from a longer lookback 'opens' array.
        
        # We need the open price at the start of the US session (UTC 13:00) on the current day.
        # This requires tracking state or a more advanced API.
        
        # Let's assume 'opens' and 'closes' are from the current day, and we're looking for the start of the session.
        # The simplest approximation: the "session open" is the open of the bar that occurred at self.us_session_start_hour.
        # If current_hour is 14, and start_hour is 13, then opens[-2] is the open of the 13:00 bar.
        
        # We need to ensure we have enough bars to calculate the true session start.
        # Let's find the open of the bar that corresponds to self.us_session_start_hour.
        # This is a challenge with the current API which only gives relative 'closes(n)'.
        
        # Given ctx.bar.timestamp, we can determine the current bar's hour.
        # To get the open of the bar at self.us_session_start_hour:
        # If current_hour = 14, start_hour = 13, then we need opens[-2]
        # If current_hour = 13, start_hour = 13, then we need opens[-1]
        
        # Let's approximate the 'session_open' as the 'opens' value that corresponds to the start hour.
        # This means we need to find how many bars ago the session started.
        
        bars_since_session_start = current_hour - self.us_session_start_hour
        if bars_since_session_start < 0: # Not yet in session or past it
            return None

        # Ensure we have enough historical data to get the session open
        if len(opens) <= bars_since_session_start:
            return None
        
        session_open_price = opens[-(bars_since_session_start + 1)] # This is the open of the bar at session_start_hour
        
        # Session move is from that session_open_price to current close
        session_move = ctx.bar.close - session_open_price
        session_move_pct = (session_move / session_open_price) * 100 if session_open_price != 0 else 0

        # Calculate median range for volatility check
        median_range = self._median_range(highs, lows, self.median_range_period)
        if median_range is None or median_range == 0:
            return None

        # Convert median range to percentage of current price
        median_range_pct = (median_range / ctx.bar.close) * 100 if ctx.bar.close != 0 else 0

        # Check for single bar spike (simplified)
        if self._is_single_bar_spike(ctx, session_move_pct):
            return None

        # Determine if the session move is decisive but not exhausted
        min_decisive_move_pct = median_range_pct * self.session_move_threshold_multiplier
        max_exhausted_move_pct = median_range_pct * self.max_session_move_factor

        signal_direction = None
        confidence = 0.0
        reason = ""

        if session_move_pct > min_decisive_move_pct and session_move_pct < max_exhausted_move_pct:
            signal_direction = "long"
            confidence = min(0.9, (session_move_pct / max_exhausted_move_pct)) # Scale confidence
            reason = "us_session_up_drift_follow"
        elif session_move_pct < -min_decisive_move_pct and session_move_pct > -max_exhausted_move_pct:
            signal_direction = "short"
            confidence = min(0.9, (abs(session_move_pct) / max_exhausted_move_pct)) # Scale confidence
            reason = "us_session_down_drift_follow"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            self.last_session_entry_bar = ctx.bar_index # Mark that we entered this session
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "session_open_price": session_open_price,
                    "current_close": ctx.bar.close,
                    "session_move_pct": session_move_pct,
                    "median_range_pct": median_range_pct,
                    "min_decisive_move_pct": min_decisive_move_pct,
                    "max_exhausted_move_pct": max_exhausted_move_pct
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Adaptive logic based on position close.
        # For example, if a position was very profitable, you could increase confidence.
        # Or if it was a loss, you could reduce confidence or increase cooldown.
        pass