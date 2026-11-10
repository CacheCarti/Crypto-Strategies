from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityClimaxFade(Strategy):
    METADATA = {
        "name": "VolatilityClimaxFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.range_period = 20
        self.volume_mult_threshold = 2.5
        self.range_mult_threshold = 2.0
        self.cooldown_bars = 10
        self.last_signal_bar = -self.cooldown_bars

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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 2)
        opens = ctx.opens(self.vol_period + 2)
        highs = ctx.highs(self.vol_period + 2)
        lows = ctx.lows(self.vol_period + 2)
        volumes = ctx.volumes(self.vol_period + 2)

        if len(closes) < self.vol_period + 2:
            return None

        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_volume = volumes[-1]
        current_range = highs[-1] - lows[-1]

        # Calculate average volume and range
        avg_volume = self._sma(volumes[:-1], self.vol_period)
        avg_range = self._sma([h - l for h, l in zip(highs[:-1], lows[:-1])], self.range_period)

        if avg_volume is None or avg_range is None:
            return None

        volume_climax = current_volume > (avg_volume * self.volume_mult_threshold)
        range_climax = current_range > (avg_range * self.range_mult_threshold)

        if not (volume_climax or range_climax):
            return None

        # Check for event boundary and next bar failure
        # Look at the previous bar as the "climax bar" and current bar as "failure to continue"
        climax_bar_open = opens[-2]
        climax_bar_close = closes[-2]
        climax_bar_high = highs[-2]
        climax_bar_low = lows[-2]
        climax_bar_range = climax_bar_high - climax_bar_low
        climax_bar_volume = volumes[-2]

        # Ensure the climax bar itself was significant
        climax_avg_volume = self._sma(volumes[:-2], self.vol_period)
        climax_avg_range = self._sma([h - l for h, l in zip(highs[:-2], lows[:-2])], self.range_period)

        if climax_avg_volume is None or climax_avg_range is None:
            return None

        climax_volume_significant = climax_bar_volume > (climax_avg_volume * self.volume_mult_threshold * 0.8) # Slightly lower threshold for the actual climax bar
        climax_range_significant = climax_bar_range > (climax_avg_range * self.range_mult_threshold * 0.8)

        if not (climax_volume_significant or climax_range_significant):
            return None

        signal = None
        confidence = 0.65 # Base confidence

        # Long Climax: large up bar, current bar fails to make new high or closes lower
        if climax_bar_close > climax_bar_open: # Previous bar was an up bar
            if closes[-1] < opens[-1] and highs[-1] < climax_bar_high: # Current bar is a down bar and failed to surpass climax high
                signal = "short"
                confidence += 0.1 if volume_climax else 0
                confidence += 0.1 if range_climax else 0
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    signal,
                    confidence=min(1.0, confidence),
                    metadata={
                        "reason": "vol_range_climax_fade_short",
                        "climax_volume_mult": climax_bar_volume / climax_avg_volume,
                        "climax_range_mult": climax_bar_range / climax_avg_range,
                        "current_volume_mult": current_volume / avg_volume,
                        "current_range_mult": current_range / avg_range,
                        "climax_bar_close": climax_bar_close,
                        "current_bar_close": closes[-1]
                    }
                )

        # Short Climax: large down bar, current bar fails to make new low or closes higher
        elif climax_bar_close < climax_bar_open: # Previous bar was a down bar
            if closes[-1] > opens[-1] and lows[-1] > climax_bar_low: # Current bar is an up bar and failed to surpass climax low
                signal = "long"
                confidence += 0.1 if volume_climax else 0
                confidence += 0.1 if range_climax else 0
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    signal,
                    confidence=min(1.0, confidence),
                    metadata={
                        "reason": "vol_range_climax_fade_long",
                        "climax_volume_mult": climax_bar_volume / climax_avg_volume,
                        "climax_range_mult": climax_bar_range / climax_avg_range,
                        "current_volume_mult": current_volume / avg_volume,
                        "current_range_mult": current_range / avg_range,
                        "climax_bar_close": climax_bar_close,
                        "current_bar_close": closes[-1]
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally adjust cooldown or confidence based on performance
        pass