from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class SessionStructure(Strategy):
    METADATA = {
        "name": "Session Structure",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_lookback_period = 6  # 6 hours for the late US session (18-24 UTC)
        self.median_range_period = 24    # Lookback for median range to gauge decisiveness
        self.cooldown_bars = 5           # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1
        self.last_session_entry_bar = -1 # To ensure only one entry per session

    def _median(self, values):
        if not values:
            return 0
        sorted_values = sorted(values)
        mid = len(sorted_values) // 2
        if len(sorted_values) % 2 == 0:
            return (sorted_values[mid - 1] + sorted_values[mid]) / 2
        else:
            return sorted_values[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check if we are in the late US session (UTC 18-24)
        is_late_us_session = 18 <= current_hour < 24

        # Exit existing position logic
        if ctx.has_position():
            # If we are holding a position and it's approaching midnight UTC, consider exiting
            # to avoid holding through session transition, unless it's a strong trend.
            # We'll let the declared_hold_seconds and SL/TP handle most exits,
            # but add a soft exit for session end.
            if current_hour == 23 and ctx.position_direction() is not None:
                # Add metadata for the exit reason
                return ctx.signal("flat", confidence=0.3, metadata={
                    "reason": "session_end_soft_exit",
                    "current_hour": current_hour,
                    "entry_price": ctx.position["entry_price"]
                })
            return None # Hold position if no exit condition met

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Only allow one entry per late US session block (18-24 UTC)
        if is_late_us_session and self.last_session_entry_bar != -1 and \
           ctx.bar_index - self.last_session_entry_bar < self.session_lookback_period: # Prevents multiple entries within same 6-hour window
            return None

        # Entry logic within the late US session
        if is_late_us_session:
            # Need enough bars for session lookback and median range calculation
            if len(closes) < max(self.session_lookback_period, self.median_range_period):
                return None

            # Calculate session move
            session_opens = ctx.opens(self.session_lookback_period)
            session_closes = ctx.closes(self.session_lookback_period)
            session_highs = ctx.highs(self.session_lookback_period)
            session_lows = ctx.lows(self.session_lookback_period)

            if len(session_opens) < self.session_lookback_period:
                return None

            # Open of the current session block
            session_start_price = session_opens[0]
            current_price = closes[-1]

            # Calculate the range of recent bars to determine a "significant" move
            recent_ranges = [h - l for h, l in zip(highs[-self.median_range_period:], lows[-self.median_range_period:])]
            median_recent_range = self._median(recent_ranges)

            # Avoid single-bar spikes: check if the current bar's range is too large relative to the session move
            current_bar_range = ctx.bar.high - ctx.bar.low
            session_move_abs = abs(current_price - session_start_price)

            # Heuristic to check if move is decisive but not exhausted / a spike
            # Move should be at least a fraction of the median recent range
            # And current bar's range shouldn't dominate the entire session move (indicating a spike)
            min_decisive_move_ratio = 0.5 # e.g., 50% of median range
            max_spike_ratio = 0.7 # e.g., current bar range should be less than 70% of session move

            if median_recent_range == 0: # Avoid division by zero
                return None

            # Bullish setup
            if current_price > session_start_price and \
               session_move_abs > min_decisive_move_ratio * median_recent_range and \
               current_bar_range < max_spike_ratio * session_move_abs:

                self.last_trade_bar = ctx.bar_index
                self.last_session_entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "late_us_session_bullish_momentum",
                        "session_start_price": session_start_price,
                        "current_price": current_price,
                        "session_move_abs": session_move_abs,
                        "median_recent_range": median_recent_range,
                        "current_bar_range": current_bar_range,
                        "current_hour": current_hour
                    }
                )

            # Bearish setup
            elif current_price < session_start_price and \
                 session_move_abs > min_decisive_move_ratio * median_recent_range and \
                 current_bar_range < max_spike_ratio * session_move_abs:

                self.last_trade_bar = ctx.bar_index
                self.last_session_entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "late_us_session_bearish_momentum",
                        "session_start_price": session_start_price,
                        "current_price": current_price,
                        "session_move_abs": session_move_abs,
                        "median_recent_range": median_recent_range,
                        "current_bar_range": current_bar_range,
                        "current_hour": current_hour
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last session entry bar if a position closes, allowing a new entry in the same session
        # if the cooldown period is also passed. This might be too aggressive,
        # but ensures the strategy can react to new setups within the same session after an early exit.
        # For this strategy, we want to enforce ONE entry per session block, so we don't reset it here.
        # self.last_session_entry_bar = -1 # Keep it commented to enforce one entry per session block
        pass