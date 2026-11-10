from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanSessionBreakout(Strategy):
    METADATA = {
        "name": "European Session Breakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Intended hold ~2 hours
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20 # For median range calculation
        self.breakout_threshold_factor = 1.2 # How much move needs to exceed median range
        self.min_move_bps = 50 # Minimum move in bps to consider a breakout
        self.max_move_bps = 300 # Max move in bps to avoid exhausted moves
        self.cooldown_bars = 5 # Bars to wait after a trade before new entry
        self.last_trade_bar = -self.cooldown_bars

        # State to track for session logic
        self.session_high = -1.0
        self.session_low = float('inf')
        self.session_open = -1.0
        self.session_start_bar_index = -1
        self.last_signal_hour = -1

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        ranges.sort()
        mid = len(ranges) // 2
        return (ranges[mid] + ranges[~mid]) / 2 if len(ranges) % 2 == 0 else ranges[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Check for cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Reset session data at UTC 7 (start of European morning)
        if current_hour == 7 and self.session_start_bar_index != ctx.bar_index:
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_open = ctx.bar.open
            self.session_start_bar_index = ctx.bar_index
            self.last_signal_hour = -1 # Allow new signal for the day
            return None # Don't trade on the first bar of the session

        # Update session high/low
        if self.session_start_bar_index != -1 and ctx.bar_index > self.session_start_bar_index:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Trading logic only during European morning (UTC 8-11)
        if 8 <= current_hour <= 11 and not ctx.has_position() and self.last_signal_hour != current_hour:
            median_range = self._median_range(highs[:-1], lows[:-1], self.lookback_period)
            if median_range is None:
                return None

            # Calculate current session move
            session_move_up = (current_price - self.session_open) / self.session_open * 10000
            session_move_down = (self.session_open - current_price) / self.session_open * 10000

            # Convert median_range to bps
            median_range_bps = median_range / self.session_open * 10000

            # Long condition: price breaks above session high and move is decisive but not exhausted
            if current_price > self.session_high and session_move_up > median_range_bps * self.breakout_threshold_factor:
                if self.min_move_bps < session_move_up < self.max_move_bps:
                    self.last_trade_bar = ctx.bar_index
                    self.last_signal_hour = current_hour
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "european_session_breakout_long",
                            "current_hour": current_hour,
                            "session_open": self.session_open,
                            "session_high": self.session_high,
                            "current_price": current_price,
                            "session_move_bps": round(session_move_up, 2),
                            "median_range_bps": round(median_range_bps, 2)
                        }
                    )

            # Short condition: price breaks below session low and move is decisive but not exhausted
            elif current_price < self.session_low and session_move_down > median_range_bps * self.breakout_threshold_factor:
                if self.min_move_bps < session_move_down < self.max_move_bps:
                    self.last_trade_bar = ctx.bar_index
                    self.last_signal_hour = current_hour
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "european_session_breakout_short",
                            "current_hour": current_hour,
                            "session_open": self.session_open,
                            "session_low": self.session_low,
                            "current_price": current_price,
                            "session_move_bps": round(session_move_down, 2),
                            "median_range_bps": round(median_range_bps, 2)
                        }
                    )

        # Exit logic: if position is open and it's past UTC 12, or the session resets
        if ctx.has_position() and current_hour == 12:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "flat",
                confidence=0.8,
                metadata={
                    "reason": "end_of_session_exit",
                    "current_hour": current_hour,
                    "entry_price": ctx.position["entry_price"],
                    "current_price": current_price
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_signal_hour to allow for a new trade the next day, if it was closed prematurely
        # Or if the strategy wants to signal again in a later session if conditions are met
        # For this strategy, we want to ensure only one trade per day, so no change here
        pass