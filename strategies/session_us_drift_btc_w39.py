from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class SessionBreakout(Strategy):
    METADATA = {
        "name": "BTC US Session Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Intended hold for 2-4 hours, max until session end
        "warmup_bars": 100,
        "required_features": [], # No specific features required for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13 # UTC
        self.us_session_end_hour = 21   # UTC (exclusive, so exit before 21:00 UTC bar)
        self.lookback_period_range = 24 # Bars for median range calculation
        self.cooldown_bars = 5          # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        # Store recent bar data for median range calculation
        self.recent_highs = collections.deque(maxlen=self.lookback_period_range)
        self.recent_lows = collections.deque(maxlen=self.lookback_period_range)
        self.recent_ranges = collections.deque(maxlen=self.lookback_period_range)

        # To ensure only one entry per US session
        self.traded_this_session = False
        self.last_session_day = -1

    def _median(self, data):
        if not data:
            return 0
        sorted_data = sorted(data)
        mid = len(sorted_data) // 2
        if len(sorted_data) % 2 == 0:
            return (sorted_data[mid - 1] + sorted_data[mid]) / 2
        return sorted_data[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Update recent highs, lows, and ranges
        self.recent_highs.append(ctx.bar.high)
        self.recent_lows.append(ctx.bar.low)
        self.recent_ranges.append(ctx.bar.high - ctx.bar.low)

        # Reset traded_this_session flag at the start of a new day
        if current_day != self.last_session_day:
            self.traded_this_session = False
            self.last_session_day = current_day

        # Exit logic: if we have a position and it's near the end of the US session
        if ctx.has_position():
            # Exit before the session end hour
            if current_hour >= self.us_session_end_hour - 1: # Exit on the bar before session_end_hour
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

            # Check for structure stop (e.g., break below previous bar's low for long, above high for short)
            if ctx.position_direction() == "long" and ctx.bar.close < lows[-2]:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "structure_stop_long", "current_close": ctx.bar.close, "previous_low": lows[-2]})
            elif ctx.position_direction() == "short" and ctx.bar.close > highs[-2]:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "structure_stop_short", "current_close": ctx.bar.close, "previous_high": highs[-2]})


        # Entry logic
        if not ctx.has_position() and not self.traded_this_session and (ctx.bar_index - self.last_trade_bar > self.cooldown_bars):
            if current_hour == self.us_session_start_hour: # US session starts
                # Calculate the range of the previous bar (this is the first bar of the US session)
                prev_bar_range = highs[-2] - lows[-2]
                
                # Ensure enough data for median range calculation
                if len(self.recent_ranges) < self.lookback_period_range:
                    return None

                median_range = self._median(list(self.recent_ranges)[:-1]) # Exclude current bar's range
                
                # Define a threshold for a "decisive" move
                decisive_move_threshold = median_range * 1.5 # e.g., 1.5x median range
                
                # Check for a decisive move in the previous bar
                if prev_bar_range > decisive_move_threshold:
                    # Check for long breakout: previous bar closed significantly higher than its open
                    if closes[-2] > opens[-2] + (prev_bar_range * 0.5): # Closed in upper half of its range
                        self.traded_this_session = True
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "us_session_open_long_breakout",
                                "entry_bar_hour": current_hour,
                                "prev_bar_range": prev_bar_range,
                                "median_range_lookback": median_range,
                                "close_open_diff": closes[-2] - opens[-2]
                            }
                        )
                    # Check for short breakout: previous bar closed significantly lower than its open
                    elif closes[-2] < opens[-2] - (prev_bar_range * 0.5): # Closed in lower half of its range
                        self.traded_this_session = True
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            metadata={
                                "reason": "us_session_open_short_breakout",
                                "entry_bar_hour": current_hour,
                                "prev_bar_range": prev_bar_range,
                                "median_range_lookback": median_range,
                                "close_open_diff": closes[-2] - opens[-2]
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We can log position details or update state here if needed
        # For this strategy, we've already handled `traded_this_session` and `last_trade_bar`
        pass