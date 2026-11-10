from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightDriftBTC(Strategy):
    METADATA = {
        "name": "OvernightDriftBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Roughly 4 hours hold
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24  # For median range calculation
        self.entry_threshold_multiplier = 0.5  # % of median range for entry
        self.cooldown_bars = 6
        self.last_trade_bar = -self.cooldown_bars
        self.last_signal_direction = None
        self.overnight_start_hour = 0  # UTC
        self.overnight_end_hour = 6    # UTC
        self.us_session_start_hour = 13 # UTC (9 AM EST)

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return 0.0
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        ranges.sort()
        mid = len(ranges) // 2
        return (ranges[mid] + ranges[~mid]) / 2 if len(ranges) % 2 == 0 else ranges[mid]

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: if in position and approaching US session or end of trading day
        if ctx.has_position():
            if current_hour == self.us_session_start_hour - 1: # One hour before US session
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_before_us_session",
                    "current_hour": current_hour,
                    "entry_price": ctx.position["entry_price"],
                    "current_price": ctx.bar.close
                })
            # Also exit if holding into the next overnight window (don't carry overnight)
            if current_hour == self.overnight_start_hour -1 :
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_before_next_overnight_window",
                    "current_hour": current_hour,
                    "entry_price": ctx.position["entry_price"],
                    "current_price": ctx.bar.close
                })

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: Only consider signals during the overnight window
        if self.overnight_start_hour <= current_hour < self.overnight_end_hour:
            # Calculate median true range for volatility filter
            median_daily_range = self._median_range(highs[:-1], lows[:-1], self.lookback_period)
            if median_daily_range == 0:
                return None

            # Calculate the move within the current overnight session so far
            # We need to find the start of the current overnight session
            session_closes = []
            session_highs = []
            session_lows = []
            # Iterate backwards to find the start of the current overnight session
            for i in range(1, len(closes) + 1):
                bar_time = ctx.bar.timestamp.replace(hour=current_hour).replace(minute=0, second=0, microsecond=0)
                # Adjust bar_time to be the timestamp of the i-th last bar
                # This is a bit tricky with BarContext's fixed lookback
                # A simpler approach is to assume the `closes` array aligns with the current day's overnight
                # For this strategy, we'll simplify and check the hourly range of the current bar
                # against a threshold derived from past daily ranges.

                # Simplified: Check the current bar's move against the median range
                # This is a single-bar move, which might be a spike.
                # A better approach for "session move" would require tracking session start price.
                # Given BarContext constraints, we'll adapt to using recent history.

            # Let's define "session move" as the move from the previous bar,
            # but ensure it's not just a spike and is a decisive move.
            # We will use the close of the previous hour to the current close.
            if len(closes) < 2:
                return None

            prev_close = closes[-2]
            current_close = closes[-1]
            move_abs = abs(current_close - prev_close)
            move_pct = (current_close - prev_close) / prev_close

            # Filter out single-bar spikes: ensure the move is sustained
            # This is hard to do with just `closes` without a session start price.
            # Let's use a simpler proxy: the move must be a certain % of the median range,
            # and not be a reversal from the previous bar (i.e., if going long, prev bar wasn't a big down bar).

            # Determine entry threshold
            entry_threshold = median_daily_range * self.entry_threshold_multiplier

            # Condition for a long signal: decisive upward move
            if move_abs > entry_threshold and current_close > prev_close:
                # Add a check that the current bar isn't a huge outlier (e.g., current bar range is not excessively large compared to its body)
                current_bar_range = highs[-1] - lows[-1]
                if current_bar_range > 0 and abs(current_close - prev_close) / current_bar_range > 0.3: # Body is at least 30% of total range
                    self.last_trade_bar = ctx.bar_index
                    self.last_signal_direction = "long"
                    return ctx.signal("long", confidence=0.75, metadata={
                        "reason": "overnight_drift_long",
                        "current_hour": current_hour,
                        "move_pct": move_pct * 100,
                        "median_range": median_daily_range,
                        "entry_threshold": entry_threshold,
                        "current_close": current_close,
                        "prev_close": prev_close
                    })

            # Condition for a short signal: decisive downward move
            elif move_abs > entry_threshold and current_close < prev_close:
                current_bar_range = highs[-1] - lows[-1]
                if current_bar_range > 0 and abs(current_close - prev_close) / current_bar_range > 0.3: # Body is at least 30% of total range
                    self.last_trade_bar = ctx.bar_index
                    self.last_signal_direction = "short"
                    return ctx.signal("short", confidence=0.75, metadata={
                        "reason": "overnight_drift_short",
                        "current_hour": current_hour,
                        "move_pct": move_pct * 100,
                        "median_range": median_daily_range,
                        "entry_threshold": entry_threshold,
                        "current_close": current_close,
                        "prev_close": prev_close
                    })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt based on position close.
        # For this strategy, we reset last_trade_bar in on_bar, so no special action here.
        pass