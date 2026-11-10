from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BTCEuropeanDrift(Strategy):
    METADATA = {
        "name": "BTC_European_Drift",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Max hold until close of session
        "warmup_bars": 30,
        "required_features": [], # No external features used
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 7  # UTC 7:00
        self.session_end_hour = 12   # UTC 12:00 (exit by this hour)
        self.min_session_move_factor = 0.003 # 0.3% move
        self.cooldown_bars = 12 # Wait 12 hours after a trade
        self.last_trade_bar_index = -self.cooldown_bars # Allow trade on first bar

        # Store daily state to ensure only one trade per session
        self.last_trade_day = -1

        # Period for median range calculation (e.g., 20 bars = 20 hours)
        self.range_period = 20

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        sorted_ranges = sorted(ranges)
        mid = len(sorted_ranges) // 2
        if len(sorted_ranges) % 2 == 0:
            return (sorted_ranges[mid - 1] + sorted_ranges[mid]) / 2
        else:
            return sorted_ranges[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period + 1)
        highs = ctx.highs(self.range_period + 1)
        lows = ctx.lows(self.range_period + 1)

        if len(closes) < self.range_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar_index + self.cooldown_bars:
            if ctx.has_position() and current_hour >= self.session_end_hour:
                # Allow exit even during cooldown if session ends
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_cooldown_override", "hour": current_hour})
            return None

        # Exit logic: Close position by session end hour
        if ctx.has_position() and current_hour >= self.session_end_hour:
            self.last_trade_bar_index = ctx.bar_index # Reset cooldown after exit
            return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "hour": current_hour})

        # Entry logic: Only consider during European morning session
        if not (self.session_start_hour <= current_hour < self.session_end_hour):
            return None

        # Ensure only one entry per day
        if current_day == self.last_trade_day:
            return None

        # Calculate session move
        # We need bars from the start of the session (or as far back as available in current window)
        # Find the start of the current session within the available bars
        session_opens = []
        session_closes = []
        session_highs = []
        session_lows = []

        # Look back up to self.session_end_hour - self.session_start_hour bars
        # This is a simplified approach, assuming session starts at self.session_start_hour
        # and we are currently within the session.
        # We need to find the 'open' of the current session.
        # For simplicity, let's just look at the move from the start of the defined window
        # up to the current bar, if it falls within the session.
        
        # Determine the start of the session for the current day
        # This requires looking at previous bars' timestamps, which is not directly available
        # in the current BarContext for arbitrary lookback.
        # A simpler approach: use the close of the previous bar as a reference for the start of the current "move"
        # within the session context, and require several bars of decisive move.

        # Let's consider the move over the last few bars within the session
        # For a 1-hour bar, consider the move over the last 3-4 hours within the session.
        # This avoids single-bar spikes.
        lookback_for_move = 3 # Look at move over last 3 hours

        if len(closes) < lookback_for_move + 1:
            return None

        # Calculate move from lookback_for_move bars ago to now
        initial_price = closes[-lookback_for_move - 1]
        current_price = closes[-1]
        
        # Ensure it's not a single-bar spike by checking the previous bar's range
        # and current bar's range relative to the overall move.
        # This is a heuristic.
        current_bar_range = highs[-1] - lows[-1]
        prev_bar_range = highs[-2] - lows[-2]

        median_range = self._median_range(highs[:-1], lows[:-1], self.range_period)
        if median_range is None:
            return None

        min_move_abs = median_range * 1.5 # Requires a move larger than median range
        # Or, require a percentage move:
        min_move_pct = self.min_session_move_factor

        session_move_abs = abs(current_price - initial_price)
        session_move_pct = session_move_abs / initial_price

        # Skip if the move is too small or if it's primarily a single bar spike
        # (e.g., if the last bar accounts for too much of the total move)
        if session_move_abs < min_move_abs or session_move_pct < min_move_pct:
            return None
        
        # Avoid entering on a bar that's already a huge candle completing the move
        # Heuristic: if current bar range is > 70% of the total move, it might be exhausted
        if current_bar_range > 0.7 * session_move_abs:
            return None

        direction = None
        if current_price > initial_price and closes[-1] > closes[-2]: # Upward drift, current bar closed higher
            direction = "long"
        elif current_price < initial_price and closes[-1] < closes[-2]: # Downward drift, current bar closed lower
            direction = "short"

        if direction and not ctx.has_position():
            self.last_trade_bar_index = ctx.bar_index
            self.last_trade_day = current_day
            return ctx.signal(
                direction,
                confidence=0.75,
                metadata={
                    "reason": f"european_session_drift_{direction}",
                    "current_hour": current_hour,
                    "session_move_pct": round(session_move_pct * 100, 2),
                    "median_range": round(median_range, 2),
                    "current_price": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: adaptive logic based on position outcome
        # self.last_trade_bar_index = ctx.bar_index # Cooldown is already set on signal
        pass