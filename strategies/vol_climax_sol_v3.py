from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityClimaxFade(Strategy):
    METADATA = {
        "name": "VolatilityClimaxFadeSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold of 4 hours
        "warmup_bars": 50,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.volume_period = 20
        self.range_period = 20
        self.cooldown_bars = 10
        self.last_trade_bar = -self.cooldown_bars - 1 # Ensure it can trade on first bar
        self.min_volume_ratio = 2.0 # Volume must be 2x average
        self.min_range_ratio = 1.5 # Range must be 1.5x average
        self.climax_threshold = 0.7 # Climax bar must close in top/bottom 30% of its range

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

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_volume = ctx.bar.volume
        current_range = current_high - current_low

        # Calculate average volume and range
        avg_volume = self._sma(volumes[:-1], self.volume_period)
        avg_range = self._sma([h - l for h, l in zip(highs[:-1], lows[:-1])], self.range_period)

        if avg_volume is None or avg_range is None:
            return None

        # Condition 1: Volume climax
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
        is_volume_climax = volume_ratio >= self.min_volume_ratio

        # Condition 2: Range climax
        range_ratio = current_range / avg_range if avg_range > 0 else 0
        is_range_climax = range_ratio >= self.min_range_ratio

        # Condition 3: Climax bar close location (for fade direction)
        # For a potential short (bearish climax), price closes near low
        # For a potential long (bullish climax), price closes near high
        if current_range == 0:
            return None

        close_location = (current_close - current_low) / current_range

        # Condition 4: Next bar fails to continue
        # This is the bar AFTER the climax bar. We are looking at the current bar as the "failure" bar.
        # This means the climax event happened on the previous bar (closes[-2], volumes[-2], etc.)
        prev_close = closes[-2]
        prev_open = opens[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]
        prev_volume = volumes[-2]
        prev_range = prev_high - prev_low

        if prev_range == 0:
            return None

        prev_volume_ratio = prev_volume / avg_volume if avg_volume > 0 else 0
        prev_range_ratio = prev_range / avg_range if avg_range > 0 else 0
        is_prev_volume_climax = prev_volume_ratio >= self.min_volume_ratio
        is_prev_range_climax = prev_range_ratio >= self.min_range_ratio

        prev_close_location = (prev_close - prev_low) / prev_range

        signal = None
        confidence = 0.6

        # Short signal: previous bar was a bearish climax, current bar fails to make new highs
        if (is_prev_volume_climax or is_prev_range_climax) and \
           prev_close_location < (1 - self.climax_threshold) and \
           current_high < prev_high: # Current bar did not exceed previous climax high
            if not ctx.has_position():
                signal = ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_climax_fade",
                        "prev_volume_ratio": prev_volume_ratio,
                        "prev_range_ratio": prev_range_ratio,
                        "prev_close_location": prev_close_location,
                        "current_high": current_high,
                        "prev_high": prev_high
                    }
                )

        # Long signal: previous bar was a bullish climax, current bar fails to make new lows
        elif (is_prev_volume_climax or is_prev_range_climax) and \
             prev_close_location > self.climax_threshold and \
             current_low > prev_low: # Current bar did not go below previous climax low
            if not ctx.has_position():
                signal = ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_climax_fade",
                        "prev_volume_ratio": prev_volume_ratio,
                        "prev_range_ratio": prev_range_ratio,
                        "prev_close_location": prev_close_location,
                        "current_low": current_low,
                        "prev_low": prev_low
                    }
                )
        
        if signal:
            self.last_trade_bar = ctx.bar_index
        
        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just reset cooldown if needed (already handled by last_trade_bar)
        pass