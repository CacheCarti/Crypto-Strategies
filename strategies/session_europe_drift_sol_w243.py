from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionBreakout(Strategy):
    METADATA = {
        "name": "SOL Session Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for 4 hours hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20  # For median range calculation
        self.min_move_ratio = 1.5  # Move must be 1.5x median range
        self.max_move_ratio = 5.0  # Avoid moves that are already 5x median range
        self.cooldown_bars = 5    # Wait 5 bars after a trade before new entry
        self.last_trade_bar = -self.cooldown_bars

        # Session boundaries (UTC)
        self.entry_start_hour = 7  # European morning open
        self.entry_end_hour = 12   # European morning close
        self.exit_hour = 12        # Exit by end of session if still open

        self.daily_entry_made = False # Flag to ensure only one entry per day (UTC day)
        self.last_day_checked = -1

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
        current_day = ctx.bar.timestamp.day

        # Reset daily entry flag at the start of a new UTC day
        if current_day != self.last_day_checked:
            self.daily_entry_made = False
            self.last_day_checked = current_day

        # Exit logic: if position open and it's 12 UTC or later, flatten
        if ctx.has_position() and current_hour >= self.exit_hour:
            if ctx.position_direction() == "long":
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close_exit", "current_hour": current_hour, "price": ctx.bar.close})
            elif ctx.position_direction() == "short":
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close_exit", "current_hour": current_hour, "price": ctx.bar.close})

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: Only within the specified entry hours and if no entry made today
        if not self.daily_entry_made and self.entry_start_hour <= current_hour < self.entry_end_hour:
            # Calculate median range over the last 'lookback_period' bars
            median_range = self._median_range(highs[:-1], lows[:-1], self.lookback_period)
            if median_range is None or median_range == 0:
                return None

            # Calculate the current bar's range
            current_bar_range = ctx.bar.high - ctx.bar.low
            current_bar_body = abs(ctx.bar.close - ctx.bar.open)

            # Avoid single-bar spikes: ensure body is a significant part of the range
            if current_bar_body / current_bar_range < 0.6: # Body must be at least 60% of total range
                return None

            # Check for decisive move
            # We are looking for a break *relative to the previous bar's close*
            # and compare it to the median range.
            prev_close = closes[-2]
            current_close = closes[-1]
            move_magnitude = abs(current_close - prev_close)

            # Ensure the move is decisive but not already exhausted
            if median_range * self.min_move_ratio <= move_magnitude <= median_range * self.max_move_ratio:
                # Check if it's a breakout (price moved significantly beyond previous bar)
                # We want to enter *after* a strong bar that closes near its high/low
                # and extends the range.
                
                # Check for long signal
                if current_close > prev_close and ctx.bar.close > ctx.bar.open: # Strong bullish bar
                    # The bar closes significantly higher than its open, indicating buying pressure
                    # and the move magnitude is within our desired range.
                    self.daily_entry_made = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "session_bullish_breakout",
                            "hour": current_hour,
                            "median_range": median_range,
                            "move_magnitude": move_magnitude,
                            "price": ctx.bar.close,
                            "prev_close": prev_close,
                            "current_bar_range": current_bar_range,
                            "current_bar_body": current_bar_body
                        }
                    )

                # Check for short signal
                elif current_close < prev_close and ctx.bar.close < ctx.bar.open: # Strong bearish bar
                    # The bar closes significantly lower than its open, indicating selling pressure
                    # and the move magnitude is within our desired range.
                    self.daily_entry_made = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "session_bearish_breakout",
                            "hour": current_hour,
                            "median_range": median_range,
                            "move_magnitude": move_magnitude,
                            "price": ctx.bar.close,
                            "prev_close": prev_close,
                            "current_bar_range": current_bar_range,
                            "current_bar_body": current_bar_body
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: You could log position details or adapt strategy parameters here.
        # For this strategy, we mainly use self.last_trade_bar for cooldown.
        pass