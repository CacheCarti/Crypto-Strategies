from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolumeClimaxReversal(Strategy):
    METADATA = {
        "name": "VolumeClimaxReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.volume_period = 20
        self.range_period = 20
        self.climax_volume_multiplier = 2.0
        self.climax_range_multiplier = 1.5
        self.cooldown_bars = 5
        self.last_signal_bar_index = -self.cooldown_bars

        # State to track for reversal detection
        self.potential_long_climax_bar = None
        self.potential_short_climax_bar = None

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

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        if len(closes) <= self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        current_volume = ctx.bar.volume
        current_range = ctx.bar.high - ctx.bar.low
        current_close = ctx.bar.close

        # Calculate average volume and range for comparison
        avg_volume = self._sma(volumes[:-1], self.volume_period) # Exclude current bar for average
        avg_range = self._sma([h - l for h, l in zip(highs[:-1], lows[:-1])], self.range_period) # Exclude current bar

        if avg_volume is None or avg_range is None:
            return None

        # Check for climax bar conditions
        is_volume_climax = current_volume > avg_volume * self.climax_volume_multiplier
        is_range_climax = current_range > avg_range * self.climax_range_multiplier

        # Check for extended move (e.g., price far from longer-term average)
        # Using Z-score of close price for trend strength
        close_zscore = self._zscore(closes, self.range_period) # Check recent price deviation

        # Climax detection logic
        if is_volume_climax and is_range_climax:
            if close_zscore > 1.5: # Strong upward move
                self.potential_long_climax_bar = {
                    "bar_index": ctx.bar_index,
                    "close": current_close,
                    "high": ctx.bar.high,
                    "low": ctx.bar.low,
                    "volume": current_volume,
                    "range": current_range,
                }
                self.potential_short_climax_bar = None # Reset opposite climax
            elif close_zscore < -1.5: # Strong downward move
                self.potential_short_climax_bar = {
                    "bar_index": ctx.bar_index,
                    "close": current_close,
                    "high": ctx.bar.high,
                    "low": ctx.bar.low,
                    "volume": current_volume,
                    "range": current_range,
                }
                self.potential_long_climax_bar = None # Reset opposite climax
        else:
            # If current bar is not a climax, check for reversal from previous climax
            if self.potential_long_climax_bar and ctx.bar_index == self.potential_long_climax_bar["bar_index"] + 1:
                # Previous bar was a potential long climax. If current bar fails to continue higher (closes lower or significantly lower than climax high)
                if current_close < self.potential_long_climax_bar["close"] and current_close < self.potential_long_climax_bar["high"]:
                    self.last_signal_bar_index = ctx.bar_index
                    self.potential_long_climax_bar = None
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "volume_climax_long_reversal",
                            "climax_bar_index": self.potential_long_climax_bar["bar_index"],
                            "climax_volume": self.potential_long_climax_bar["volume"],
                            "climax_range": self.potential_long_climax_bar["range"],
                            "climax_close_zscore": close_zscore,
                            "current_close": current_close,
                            "avg_volume": avg_volume,
                            "avg_range": avg_range,
                        },
                    )
                self.potential_long_climax_bar = None # Reset after checking
            
            elif self.potential_short_climax_bar and ctx.bar_index == self.potential_short_climax_bar["bar_index"] + 1:
                # Previous bar was a potential short climax. If current bar fails to continue lower (closes higher or significantly higher than climax low)
                if current_close > self.potential_short_climax_bar["close"] and current_close > self.potential_short_climax_bar["low"]:
                    self.last_signal_bar_index = ctx.bar_index
                    self.potential_short_climax_bar = None
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "volume_climax_short_reversal",
                            "climax_bar_index": self.potential_short_climax_bar["bar_index"],
                            "climax_volume": self.potential_short_climax_bar["volume"],
                            "climax_range": self.potential_short_climax_bar["range"],
                            "climax_close_zscore": close_zscore,
                            "current_close": current_close,
                            "avg_volume": avg_volume,
                            "avg_range": avg_range,
                        },
                    )
                self.potential_short_climax_bar = None # Reset after checking

        # If a position is open, check for exit conditions
        if ctx.has_position():
            # For simplicity, rely on declared SL/TP and horizon for this strategy
            # More advanced exit logic could be added here, e.g., partial retracement
            pass

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset climax detection state as a new trading cycle begins
        self.potential_long_climax_bar = None
        self.potential_short_climax_bar = None
        # Optionally, adapt parameters based on position outcome
        # e.g., if position["return_bps"] < 0: self.cooldown_bars += 1