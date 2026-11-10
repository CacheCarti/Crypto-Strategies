from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityWickRejection(Strategy):
    METADATA = {
        "name": "VolatilityWickRejection",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, aiming for ~1 bar hold
        "warmup_bars": 30,
        "required_features": [], # No external features needed for this pure price-action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_wick_ratio = 0.6  # Minimum ratio of wick to total bar range
        self.min_volume_multiplier = 1.5 # Volume must be at least this much higher than avg
        self.cooldown_bars = 5 # Bars to wait after a signal before considering a new one
        self.last_signal_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        # Periods for volume and price range averages
        self.volume_period = 20
        self.range_period = 20

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        opens = ctx.opens(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars + 1:
            return None # Not enough data for warmup

        # Check cooldown
        if ctx.bar_index <= self.last_signal_bar_index + self.cooldown_bars:
            return None

        # Calculate average volume and range for comparison
        avg_volume = self._sma(volumes[:-1], self.volume_period) # Exclude current bar
        avg_range = self._sma([h - l for h, l in zip(highs[:-1], lows[:-1])], self.range_period) # Exclude current bar

        if avg_volume is None or avg_range is None:
            return None

        # Conditions for a rejection wick on the previous bar (bar - 1)
        prev_open = opens[-2]
        prev_close = closes[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]
        prev_volume = volumes[-2]

        prev_bar_range = prev_high - prev_low
        if prev_bar_range == 0:
            return None

        is_bullish_bar = prev_close > prev_open
        is_bearish_bar = prev_close < prev_open

        long_wick_rejection = False
        short_wick_rejection = False
        wick_ratio = 0.0

        if is_bullish_bar: # Potential long wick (upper rejection)
            upper_wick = prev_high - prev_close
            lower_wick = prev_open - prev_low
            body = prev_close - prev_open
            if upper_wick > body and upper_wick / prev_bar_range >= self.min_wick_ratio:
                long_wick_rejection = True
                wick_ratio = upper_wick / prev_bar_range
        elif is_bearish_bar: # Potential short wick (lower rejection)
            lower_wick = prev_open - prev_low
            upper_wick = prev_high - prev_close
            body = prev_open - prev_close
            if lower_wick > body and lower_wick / prev_bar_range >= self.min_wick_ratio:
                short_wick_rejection = True
                wick_ratio = lower_wick / prev_bar_range

        # Check for unusually high volume on the rejection wick bar
        high_volume_event = prev_volume > avg_volume * self.min_volume_multiplier

        # Current bar (bar 0) for confirmation
        current_open = opens[-1]
        current_close = closes[-1]
        current_high = highs[-1]
        current_low = lows[-1]

        signal_direction = None
        reason = ""

        # Long Entry: Previous bar was a lower wick rejection, current bar closes higher
        if short_wick_rejection and high_volume_event and current_close > current_open and current_close > prev_close:
            signal_direction = "long"
            reason = "high_volume_lower_wick_rejection_confirmed_up"
        # Short Entry: Previous bar was an upper wick rejection, current bar closes lower
        elif long_wick_rejection and high_volume_event and current_close < current_open and current_close < prev_close:
            signal_direction = "short"
            reason = "high_volume_upper_wick_rejection_confirmed_down"

        if ctx.has_position():
            # If we have a position, don't generate new entry signals
            # If we want to flip, we would generate the opposite signal
            if ctx.position_direction() == "long" and signal_direction == "short":
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal("short", confidence=0.7, metadata={
                    "reason": "flip_to_short_on_rejection_confirmation",
                    "wick_ratio": wick_ratio,
                    "prev_volume": prev_volume,
                    "avg_volume_20": avg_volume,
                    "prev_bar_range": prev_bar_range,
                    "current_close": current_close,
                    "current_bar_index": ctx.bar_index
                })
            elif ctx.position_direction() == "short" and signal_direction == "long":
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal("long", confidence=0.7, metadata={
                    "reason": "flip_to_long_on_rejection_confirmation",
                    "wick_ratio": wick_ratio,
                    "prev_volume": prev_volume,
                    "avg_volume_20": avg_volume,
                    "prev_bar_range": prev_bar_range,
                    "current_close": current_close,
                    "current_bar_index": ctx.bar_index
                })
            return None # Hold existing position

        if signal_direction:
            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(signal_direction, confidence=0.8, metadata={
                "reason": reason,
                "wick_ratio": wick_ratio,
                "prev_volume": prev_volume,
                "avg_volume_20": avg_volume,
                "prev_bar_range": prev_bar_range,
                "current_close": current_close,
                "current_bar_index": ctx.bar_index
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this exploration strategy, just reset cooldown
        pass