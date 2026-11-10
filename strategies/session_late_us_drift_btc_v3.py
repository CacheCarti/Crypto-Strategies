from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class SessionDriftStrategy(Strategy):
    METADATA = {
        "name": "SessionDriftStrategy_BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4, # Target 4-hour hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20 # For median range calculation
        self.min_range_factor = 0.005 # 0.5% move required to be "decisive"
        self.entry_threshold_factor = 0.8 # Multiplier for median range to trigger
        self.cooldown_bars = 5 # Bars to wait after a trade before re-entering
        self.last_trade_bar = -self.cooldown_bars

        # Session boundaries (UTC)
        self.late_us_session_start_hour = 18
        self.late_us_session_end_hour = 24 # End of the day
        self.daily_reset_hour = 0 # Midnight UTC

        self.traded_today = False # To ensure only one trade per daily cycle

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        return statistics.median(ranges)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.lookback_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Daily reset for `traded_today` flag
        if current_hour == self.daily_reset_hour and self.traded_today:
            self.traded_today = False

        # Cooldown period
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Exit logic: if we have a position and it's near the end of the trading day (UTC 0)
        if ctx.has_position() and current_hour >= self.late_us_session_end_hour - 1: # Exit before UTC 0
            # A simple "structure stop" is implicit in the declared SL/TP,
            # but we can add a time-based exit to prevent holding overnight into unknown conditions.
            return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        # Entry logic: Only consider entering during the late US session
        if not ctx.has_position() and not self.traded_today and \
           current_hour >= self.late_us_session_start_hour and \
           current_hour < self.late_us_session_end_hour:

            median_range = self._median_range(highs[:-1], lows[:-1], self.lookback_period)
            if median_range is None:
                return None

            # Calculate the move during the current bar
            current_bar_move = closes[-1] - closes[-2]
            current_bar_range = highs[-1] - lows[-1]

            # Check for decisiveness and not already exhausted (i.e., not a single-bar spike)
            # A "decisive" move means a significant portion of the median range
            # A "not exhausted" move means the current bar's range isn't excessively large compared to the move itself
            # (i.e., not a massive wick indicating rejection)
            
            # Long condition: price moved up decisively, not a massive spike
            if current_bar_move > median_range * self.entry_threshold_factor and \
               current_bar_move > closes[-2] * self.min_range_factor and \
               current_bar_range < median_range * 2.0: # avoid extremely large single bars

                self.traded_today = True
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "late_us_drift_long",
                        "current_hour": current_hour,
                        "median_range": median_range,
                        "current_bar_move": current_bar_move,
                        "current_bar_range": current_bar_range,
                    }
                )

            # Short condition: price moved down decisively, not a massive spike
            elif current_bar_move < -median_range * self.entry_threshold_factor and \
                 current_bar_move < -closes[-2] * self.min_range_factor and \
                 current_bar_range < median_range * 2.0: # avoid extremely large single bars

                self.traded_today = True
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "late_us_drift_short",
                        "current_hour": current_hour,
                        "median_range": median_range,
                        "current_bar_move": current_bar_move,
                        "current_bar_range": current_bar_range,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We can log the close for debugging or adaptive logic here
        pass