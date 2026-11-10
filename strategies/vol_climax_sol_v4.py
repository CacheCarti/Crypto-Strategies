from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolClimaxFade(Strategy):
    METADATA = {
        "name": "SolVolClimaxFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this pure OHLCV strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.volume_period = 20
        self.range_period = 20
        self.cooldown_bars = 12 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # Thresholds for climax detection (adjust as needed)
        self.volume_climax_factor = 2.5 # Current volume > 2.5 * avg_volume
        self.range_climax_factor = 2.0 # Current range > 2.0 * avg_range
        self.reversal_candle_factor = 0.5 # Next bar closes within 50% of the climax bar's range
        self.reversal_close_strength = 0.4 # Next bar closes in bottom 40% for long fade, top 40% for short fade

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.volume_period + 1)
        opens = ctx.opens(self.volume_period + 1)
        highs = ctx.highs(self.volume_period + 1)
        lows = ctx.lows(self.volume_period + 1)
        volumes = ctx.volumes(self.volume_period + 1)

        if len(closes) < self.volume_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Regime filtering: Only trade in 'normal' or 'volatile' regimes
        # Avoid 'crisis' or 'calm' for this pattern
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "CALM"]:
             return None

        # Calculate average volume and range
        avg_volume = self._sma(volumes[:-1], self.volume_period)
        if avg_volume is None or avg_volume == 0:
            return None
        
        ranges = [h - l for h, l in zip(highs, lows)]
        avg_range = self._sma(ranges[:-1], self.range_period)
        if avg_range is None or avg_range == 0:
            return None

        current_volume = volumes[-1]
        current_range = highs[-1] - lows[-1]

        # Check for climax bar (previous bar)
        prev_volume = volumes[-2]
        prev_range = highs[-2] - lows[-2]
        prev_open = opens[-2]
        prev_close = closes[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]

        is_volume_climax = prev_volume > avg_volume * self.volume_climax_factor
        is_range_climax = prev_range > avg_range * self.range_climax_factor

        long_climax_candidate = False
        short_climax_candidate = False

        if is_volume_climax and is_range_climax:
            # Check if climax bar was a strong move up (potential short fade)
            if prev_close > prev_open and (prev_close - prev_low) / prev_range > 0.6: # Closes near high
                short_climax_candidate = True
            # Check if climax bar was a strong move down (potential long fade)
            elif prev_close < prev_open and (prev_high - prev_close) / prev_range > 0.6: # Closes near low
                long_climax_candidate = True
        
        # If we have a position, check for exit conditions (optional, but good practice)
        if ctx.has_position():
            # This strategy relies on declared SL/TP for exits, but could add time-based or counter-signal exits here
            return None

        # Entry logic
        if not ctx.has_position():
            # Fade Short: Previous bar was a strong up-climax, current bar fails to continue up
            if short_climax_candidate:
                # Current bar closes lower than previous close, and within a reasonable range of previous close
                # And current bar's close is in the lower portion of its own range
                if closes[-1] < prev_close and (highs[-1] - lows[-1]) < (prev_range * self.reversal_candle_factor):
                    if (closes[-1] - lows[-1]) / (highs[-1] - lows[-1]) < self.reversal_close_strength: # Closes in bottom 40% of current bar
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            metadata={
                                "reason": "volume_range_climax_fade_short",
                                "climax_bar_volume_ratio": prev_volume / avg_volume,
                                "climax_bar_range_ratio": prev_range / avg_range,
                                "current_bar_close_relative_to_climax": (closes[-1] - prev_close) / prev_range,
                                "current_bar_close_position_in_range": (closes[-1] - lows[-1]) / (highs[-1] - lows[-1])
                            }
                        )

            # Fade Long: Previous bar was a strong down-climax, current bar fails to continue down
            elif long_climax_candidate:
                # Current bar closes higher than previous close, and within a reasonable range of previous close
                # And current bar's close is in the upper portion of its own range
                if closes[-1] > prev_close and (highs[-1] - lows[-1]) < (prev_range * self.reversal_candle_factor):
                    if (highs[-1] - closes[-1]) / (highs[-1] - lows[-1]) < self.reversal_close_strength: # Closes in top 40% of current bar
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            metadata={
                                "reason": "volume_range_climax_fade_long",
                                "climax_bar_volume_ratio": prev_volume / avg_volume,
                                "climax_bar_range_ratio": prev_range / avg_range,
                                "current_bar_close_relative_to_climax": (closes[-1] - prev_close) / prev_range,
                                "current_bar_close_position_in_range": (closes[-1] - lows[-1]) / (highs[-1] - lows[-1])
                            }
                        )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We could adapt parameters here based on performance, but for now, just log.
        pass