from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEuropeDrift(Strategy):
    METADATA = {
        "name": "SOL Europe Drift",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 3, # Aim for 3-hour hold
        "warmup_bars": 50,
        "required_features": [], # No specific features needed beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7 AM
        self.entry_hour_end = 12   # UTC 12 PM (noon)
        self.exit_hour = 12        # UTC 12 PM (noon) - hard exit boundary
        self.period_range = 24     # Lookback for median range
        self.move_threshold_multiplier = 1.5 # How many times median range for a decisive move
        self.cooldown_bars = 5     # Cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # Store last day's closing price to detect decisive moves
        self.last_day_close = None
        self.traded_today = False

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        ranges.sort()
        mid = len(ranges) // 2
        if len(ranges) % 2 == 0:
            return (ranges[mid - 1] + ranges[mid]) / 2
        else:
            return ranges[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period_range + 1)
        highs = ctx.highs(self.period_range + 1)
        lows = ctx.lows(self.period_range + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset daily trade flag at the start of a new day (UTC midnight)
        if current_hour == 0 and not ctx.has_position():
            self.traded_today = False
            self.last_day_close = closes[-1] # Update last day's close for range calculation

        # Exit logic
        if ctx.has_position():
            # Hard exit at self.exit_hour (12 PM UTC)
            if current_hour >= self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_end_time_exit",
                    "current_hour": current_hour,
                    "exit_boundary": self.exit_hour
                })
            # Or if it's past the entry window and we're still in a position, hold until exit hour.
            # This logic means we don't try to re-enter if we're already in a trade and past the entry window.
            return ctx.signal("hold")

        # Entry logic
        if self.traded_today or ctx.bar_index < self.last_trade_bar_index + self.cooldown_bars:
            return None # Cooldown or already traded today

        if not (self.entry_hour_start <= current_hour < self.entry_hour_end):
            return None # Not within entry window

        # Ensure we have a last day close for range calculation
        if self.last_day_close is None:
            return None

        median_range = self._median_range(highs[:-1], lows[:-1], self.period_range)
        if median_range is None or median_range == 0:
            return None

        current_price = closes[-1]
        previous_close = closes[-2] # Close of the previous bar

        # Calculate the move from the previous bar's close to the current close
        # This helps detect the "decisive move" within the session
        session_move = current_price - previous_close

        # Check for a decisive move, but not an exhausted single-bar spike
        # We want the current bar to be a continuation or confirmation of a trend, not just a one-off spike
        # The move should be significant compared to the median range.
        move_magnitude = abs(session_move)
        threshold = median_range * self.move_threshold_multiplier

        if move_magnitude > threshold:
            # Check for a single-bar spike rejection: if the current bar's body is too small compared to its total range,
            # it might be a spike that immediately retraced. We want a sustained move.
            bar_range = highs[-1] - lows[-1]
            bar_body = abs(current_price - opens[-1])
            if bar_range > 0 and bar_body / bar_range < 0.3: # If body is less than 30% of total bar range, it's a weak signal
                return None

            confidence = 0.65 # Base confidence for a decisive move

            if session_move > 0: # Bullish drift
                self.traded_today = True
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "sol_europe_drift_long",
                        "current_hour": current_hour,
                        "session_move": session_move,
                        "move_threshold_multiplier": self.move_threshold_multiplier,
                        "median_range": median_range,
                        "bar_body_to_range_ratio": bar_body / bar_range
                    }
                )
            elif session_move < 0: # Bearish drift
                self.traded_today = True
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "sol_europe_drift_short",
                        "current_hour": current_hour,
                        "session_move": session_move,
                        "move_threshold_multiplier": self.move_threshold_multiplier,
                        "median_range": median_range,
                        "bar_body_to_range_ratio": bar_body / bar_range
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy, simply log for observation
        pass