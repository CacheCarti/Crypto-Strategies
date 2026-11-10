from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightSessionDriftSOL(Strategy):
    METADATA = {
        "name": "Overnight Session Drift SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_start_hour = 0  # UTC 00:00
        self.overnight_end_hour = 6    # UTC 06:00 (exclusive)
        self.session_move_threshold_ratio = 0.75 # Fraction of recent median range
        self.median_range_period = 20
        self.min_move_bars = 2 # Minimum bars for a decisive move
        self.max_move_bars = 5 # Maximum bars for a decisive move
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.cooldown_bars

        # For tracking session high/low for structure stop
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_start_price = None
        self.session_active = False
        self.entry_bar_index = -1

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        ranges.sort()
        mid = len(ranges) // 2
        return (ranges[mid] + ranges[~mid]) / 2 if len(ranges) % 2 == 0 else ranges[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_range_period + self.max_move_bars)
        highs = ctx.highs(self.median_range_period + self.max_move_bars)
        lows = ctx.lows(self.median_range_period + self.max_move_bars)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            if ctx.has_position():
                # If in position during cooldown, manage stop/profit
                if ctx.position["direction"] == "long":
                    if ctx.bar.low < self.session_low: # Structure stop
                        return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_low_broken", "current_low": ctx.bar.low, "session_low_stop": self.session_low})
                elif ctx.position["direction"] == "short":
                    if ctx.bar.high > self.session_high: # Structure stop
                        return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_high_broken", "current_high": ctx.bar.high, "session_high_stop": self.session_high})
            return None

        # Session boundary detection and reset
        if current_hour == self.overnight_start_hour and not self.session_active:
            # Start of a new overnight session
            self.session_active = True
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_price = ctx.bar.open # Open of the first bar in the session
            # Reset indicators for fresh session analysis
            return None

        if current_hour == self.overnight_start_hour and self.session_active:
            # Already in a session, update session high/low
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        if current_hour == self.overnight_end_hour and self.session_active:
            # End of overnight session window, reset for next day
            self.session_active = False
            if ctx.has_position():
                # Exit position before next US session
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "overnight_session_end", "current_price": ctx.bar.close})

        if not self.session_active:
            return None

        # Logic for entering a trade
        if not ctx.has_position():
            # Only consider entry if within the overnight session and not in cooldown
            if current_hour >= self.overnight_start_hour and current_hour < self.overnight_end_hour:
                median_range = self._median_range(ctx.highs(self.median_range_period), ctx.lows(self.median_range_period), self.median_range_period)
                if median_range is None:
                    return None

                # Look for a decisive move within the last self.min_move_bars to self.max_move_bars
                for i in range(self.min_move_bars, self.max_move_bars + 1):
                    if len(closes) < i + 1:
                        continue

                    # Check for upward move
                    move_long = closes[-1] - closes[-(i+1)]
                    if move_long > median_range * self.session_move_threshold_ratio:
                        # Ensure no single-bar spike dominates
                        if (closes[-1] - closes[-2]) / move_long < 0.8: # Check if current bar is not >80% of the move
                            self.last_trade_bar = ctx.bar_index
                            self.entry_bar_index = ctx.bar_index
                            # Set initial structure stop at session low
                            self.session_low = min(self.session_low, closes[-(i+1)]) # Use low of the move start
                            return ctx.signal("long", confidence=0.75,
                                              metadata={"reason": "overnight_drift_long",
                                                        "move_bars": i,
                                                        "move_magnitude": move_long,
                                                        "median_range": median_range,
                                                        "entry_price": ctx.bar.close,
                                                        "session_low_stop": self.session_low})

                    # Check for downward move
                    move_short = closes[-(i+1)] - closes[-1]
                    if move_short > median_range * self.session_move_threshold_ratio:
                        # Ensure no single-bar spike dominates
                        if (closes[-2] - closes[-1]) / move_short < 0.8: # Check if current bar is not >80% of the move
                            self.last_trade_bar = ctx.bar_index
                            self.entry_bar_index = ctx.bar_index
                            # Set initial structure stop at session high
                            self.session_high = max(self.session_high, closes[-(i+1)]) # Use high of the move start
                            return ctx.signal("short", confidence=0.75,
                                              metadata={"reason": "overnight_drift_short",
                                                        "move_bars": i,
                                                        "move_magnitude": move_short,
                                                        "median_range": median_range,
                                                        "entry_price": ctx.bar.close,
                                                        "session_high_stop": self.session_high})
        elif ctx.has_position():
            # If in position, update session high/low for trailing structure stop
            if ctx.position["direction"] == "long":
                self.session_low = max(self.session_low, ctx.bar.low) # Trail session low up
                if ctx.bar.low < self.session_low and ctx.bar_index > self.entry_bar_index + self.min_move_bars: # Only apply after initial move
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "structure_stop_long", "current_low": ctx.bar.low, "session_low_stop": self.session_low})
            elif ctx.position["direction"] == "short":
                self.session_high = min(self.session_high, ctx.bar.high) # Trail session high down
                if ctx.bar.high > self.session_high and ctx.bar_index > self.entry_bar_index + self.min_move_bars: # Only apply after initial move
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "structure_stop_short", "current_high": ctx.bar.high, "session_high_stop": self.session_high})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        # Reset session high/low for next entry opportunity, but keep session_active state
        self.session_high = -math.inf
        self.session_low = math.inf