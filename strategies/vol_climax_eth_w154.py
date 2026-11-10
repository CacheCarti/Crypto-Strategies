from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityClimaxFade(Strategy):
    METADATA = {
        "name": "Volatility Climax Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # ~3 hours
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this core logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.vol_threshold_multiplier = 2.0 # How many stddevs above avg volume
        self.range_threshold_multiplier = 1.5 # How many stddevs above avg range
        self.cooldown_bars = 10 # Bars to wait after a trade closes
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 2)
        highs = ctx.highs(self.vol_period + 2)
        lows = ctx.lows(self.vol_period + 2)
        volumes = ctx.volumes(self.vol_period + 2)

        if len(closes) < self.vol_period + 2:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]
        current_high = highs[-1]
        current_low = lows[-1]
        current_volume = volumes[-1]
        prev_volume = volumes[-2]

        # Calculate average volume and its stddev
        recent_volumes = list(volumes[:-1]) # Exclude current bar for average
        avg_volume = self._sma(recent_volumes, self.vol_period)
        std_dev_volume = self._stddev(recent_volumes, self.vol_period)

        # Calculate average true range (ATR) and its stddev
        # We need true range for the last (vol_period + 1) bars to get avg for vol_period
        true_ranges = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            true_ranges.append(tr)
        
        # We need true_ranges for the last (vol_period + 1) bars to calculate avg_range for the last vol_period bars
        if len(true_ranges) < self.vol_period + 1:
            return None

        # Use true_ranges up to the previous bar for calculating average and stddev
        recent_true_ranges = list(true_ranges[:-1])
        avg_range = self._sma(recent_true_ranges, self.vol_period)
        std_dev_range = self._stddev(recent_true_ranges, self.vol_period)

        if avg_volume is None or std_dev_volume is None or avg_range is None or std_dev_range is None:
            return None

        # Conditions for a volatility climax (previous bar)
        vol_climax_volume = (prev_volume > avg_volume + self.vol_threshold_multiplier * std_dev_volume)
        vol_climax_range = (highs[-2] - lows[-2] > avg_range + self.range_threshold_multiplier * std_dev_range)

        if ctx.has_position():
            # If we have a position, don't enter another
            return None

        # Long Entry: Previous bar was a high-volume, high-range bearish climax, and current bar fails to continue down
        if vol_climax_volume and vol_climax_range and (closes[-2] < opens[-2]): # Previous bar was bearish
            # Current bar fails to continue down (e.g., closes higher than previous close, or significantly off low)
            if current_close > prev_close:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.75, metadata={
                    "reason": "bearish_climax_fade_long",
                    "prev_volume": prev_volume,
                    "avg_volume": avg_volume,
                    "prev_range": highs[-2] - lows[-2],
                    "avg_range": avg_range,
                    "current_close": current_close,
                    "prev_close": prev_close
                })

        # Short Entry: Previous bar was a high-volume, high-range bullish climax, and current bar fails to continue up
        if vol_climax_volume and vol_climax_range and (closes[-2] > opens[-2]): # Previous bar was bullish
            # Current bar fails to continue up (e.g., closes lower than previous close, or significantly off high)
            if current_close < prev_close:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.75, metadata={
                    "reason": "bullish_climax_fade_short",
                    "prev_volume": prev_volume,
                    "avg_volume": avg_volume,
                    "prev_range": highs[-2] - lows[-2],
                    "avg_range": avg_range,
                    "current_close": current_close,
                    "prev_close": prev_close
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to current bar_index to enforce cooldown
        self.last_trade_bar = ctx.bar_index
        # Optional: Log the close reason
        # print(f"Position closed: {position['direction']} at {ctx.bar.timestamp} with return {position['return_bps']} bps. Reason: {position.get('exit_reason')}")