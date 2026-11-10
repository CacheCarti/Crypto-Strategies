from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ClimaxReversal(Strategy):
    METADATA = {
        "name": "Climax Reversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"], # Using funding rate as a proxy for market sentiment/overextension
    }

    def initialize(self, ctx: BarContext) -> None:
        self.climax_period = 20 # Period to look for climax volume/range
        self.range_multiplier = 1.5 # How much larger than average range a climax bar should be
        self.volume_multiplier = 2.0 # How much larger than average volume a climax bar should be
        self.reversal_confirmation_bars = 1 # How many bars to wait for confirmation after climax
        self.cooldown_bars = 5 # Bars to wait after a trade closes before entering again
        self.last_trade_bar = -self.cooldown_bars

        # For adaptive sizing based on recent performance
        self.win_streak = 0
        self.loss_streak = 0
        self.base_confidence = 0.5
        self.max_confidence = 0.8
        self.min_confidence = 0.3

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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        closes = ctx.closes(self.climax_period + 1)
        highs = ctx.highs(self.climax_period + 1)
        lows = ctx.lows(self.climax_period + 1)
        volumes = ctx.volumes(self.climax_period + 1)

        if len(closes) < self.climax_period + 1:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_volume = ctx.bar.volume

        # Calculate average range and volume
        recent_ranges = [h - l for h, l in zip(highs[:-1], lows[:-1])]
        recent_volumes = volumes[:-1]

        avg_range = self._sma(recent_ranges, self.climax_period)
        avg_volume = self._sma(recent_volumes, self.climax_period)

        if avg_range is None or avg_volume is None:
            return None

        current_bar_range = current_high - current_low

        # Check for climax bar conditions
        is_climax_bar = (
            current_bar_range > avg_range * self.range_multiplier and
            current_volume > avg_volume * self.volume_multiplier
        )

        if not is_climax_bar:
            return None

        # Determine if it's an extended upward or downward move ending in climax
        # Using a simple comparison to previous bars, could use EMA or trend detection
        prev_closes = closes[-self.climax_period-1:-1] # Closes before current bar
        if len(prev_closes) < self.climax_period:
            return None

        # Calculate trend before climax
        # Simple linear regression slope or EMA comparison for trend detection
        # For simplicity, let's use the direction of the last few bars
        trend_up = all(closes[i] > closes[i-1] for i in range(len(closes) - 5, len(closes) - 1))
        trend_down = all(closes[i] < closes[i-1] for i in range(len(closes) - 5, len(closes) - 1))

        signal_direction = None
        reason = ""

        # Long Climax Reversal (Fade a strong downtrend climax)
        if trend_down and current_close > closes[-2]: # Climax bar closes higher than previous close
            # Check for reversal confirmation (next bar fails to continue down)
            # This logic needs to consider the current bar as the climax, and the *next* bar for confirmation
            # Since we only have current bar data, we assume the climax just happened on the *previous* bar
            # and we are now on the confirmation bar.
            # So, re-evaluate climax on closes[-2] (prev bar) and confirmation on closes[-1] (current bar)
            climax_bar_idx = -2
            climax_close = closes[climax_bar_idx]
            climax_high = highs[climax_bar_idx]
            climax_low = lows[climax_bar_idx]
            climax_volume = volumes[climax_bar_idx]
            climax_bar_range = climax_high - climax_low

            prev_avg_range = self._sma([h - l for h, l in zip(highs[:-2], lows[:-2])], self.climax_period)
            prev_avg_volume = self._sma(volumes[:-2], self.climax_period)

            if prev_avg_range and prev_avg_volume and \
               climax_bar_range > prev_avg_range * self.range_multiplier and \
               climax_volume > prev_avg_volume * self.volume_multiplier:
                # Previous bar was climax, current bar is confirmation
                if current_close > climax_close: # Current bar closes above climax bar close
                    signal_direction = "long"
                    reason = "downtrend_climax_reversal_confirmation"

        # Short Climax Reversal (Fade a strong uptrend climax)
        elif trend_up and current_close < closes[-2]: # Climax bar closes lower than previous close
            # Same logic, climax on closes[-2], confirmation on closes[-1]
            climax_bar_idx = -2
            climax_close = closes[climax_bar_idx]
            climax_high = highs[climax_bar_idx]
            climax_low = lows[climax_bar_idx]
            climax_volume = volumes[climax_bar_idx]
            climax_bar_range = climax_high - climax_low

            prev_avg_range = self._sma([h - l for h, l in zip(highs[:-2], lows[:-2])], self.climax_period)
            prev_avg_volume = self._sma(volumes[:-2], self.climax_period)

            if prev_avg_range and prev_avg_volume and \
               climax_bar_range > prev_avg_range * self.range_multiplier and \
               climax_volume > prev_avg_volume * self.volume_multiplier:
                # Previous bar was climax, current bar is confirmation
                if current_close < climax_close: # Current bar closes below climax bar close
                    signal_direction = "short"
                    reason = "uptrend_climax_reversal_confirmation"

        # Adaptive confidence based on win/loss streak
        confidence = self.base_confidence
        if self.win_streak > 0:
            confidence = min(self.max_confidence, self.base_confidence + self.win_streak * 0.05)
        elif self.loss_streak > 0:
            confidence = max(self.min_confidence, self.base_confidence - self.loss_streak * 0.05)

        # Funding rate as a filter for overextension
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        if signal_direction == "long" and funding_rate < -0.0001: # Very negative funding suggests extreme bearishness, good for long fade
            confidence = min(self.max_confidence, confidence * 1.1)
        elif signal_direction == "short" and funding_rate > 0.0001: # Very positive funding suggests extreme bullishness, good for short fade
            confidence = min(self.max_confidence, confidence * 1.1)
        else: # If funding rate is neutral or contradictory, reduce confidence slightly
            confidence = max(self.min_confidence, confidence * 0.9)


        if ctx.has_position():
            # If we have a position, don't generate new entry signals
            # Instead, check if we need to exit (e.g. if conditions reverse)
            if ctx.position_direction() == "long" and signal_direction == "short":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=confidence, metadata={
                    "reason": "climax_reversal_flip_short",
                    "current_close": current_close,
                    "climax_volume_mult": current_volume / avg_volume,
                    "climax_range_mult": current_bar_range / avg_range,
                    "funding_rate": funding_rate,
                })
            elif ctx.position_direction() == "short" and signal_direction == "long":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=confidence, metadata={
                    "reason": "climax_reversal_flip_long",
                    "current_close": current_close,
                    "climax_volume_mult": current_volume / avg_volume,
                    "climax_range_mult": current_bar_range / avg_range,
                    "funding_rate": funding_rate,
                })
            return None # Hold current position if no opposing signal

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_close": current_close,
                    "climax_volume_mult": current_volume / avg_volume,
                    "climax_range_mult": current_bar_range / avg_range,
                    "avg_range": avg_range,
                    "avg_volume": avg_volume,
                    "funding_rate": funding_rate,
                    "win_streak": self.win_streak,
                    "loss_streak": self.loss_streak,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        if position["return_bps"] > 0:
            self.win_streak += 1
            self.loss_streak = 0
        else:
            self.loss_streak += 1
            self.win_streak = 0
        # Reset cooldown after trade close
        self.last_trade_bar = ctx.bar_index