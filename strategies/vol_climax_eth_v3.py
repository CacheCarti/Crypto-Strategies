from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityClimaxFade(Strategy):
    METADATA = {
        "name": "VolatilityClimaxFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, aiming for a few bars hold
        "warmup_bars": 50, # Need enough bars for ATR and volume calculations
        "required_features": [], # No external features needed for this pure OHLCV strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.volume_period = 20
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # Thresholds for climax detection
        self.range_climax_multiplier = 2.5 # Current range must be X times avg ATR
        self.volume_climax_multiplier = 2.0 # Current volume must be X times avg volume

        # Fade condition
        self.fade_reversal_pct = 0.005 # Price must reverse at least 0.5% from climax high/low

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Calculate indicators
        current_atr = self._atr(highs, lows, closes, self.atr_period)
        avg_atr = self._atr(highs[:-1], lows[:-1], closes[:-1], self.atr_period) # Average up to previous bar
        avg_volume = self._sma(volumes[:-1], self.volume_period) # Average up to previous bar

        if current_atr is None or avg_atr is None or avg_volume is None or avg_volume == 0:
            return None

        current_range = ctx.bar.high - ctx.bar.low
        current_volume = ctx.bar.volume

        is_range_climax = current_range > (avg_atr * self.range_climax_multiplier)
        is_volume_climax = current_volume > (avg_volume * self.volume_climax_multiplier)

        # Check for Climax bar (previous bar was climax, current bar is the fade)
        # We need the previous bar's OHLCV to check if it was a climax
        if ctx.bar_index < self.warmup_bars + 1: # Need enough history for previous bar's indicators
            return None

        prev_highs = ctx.highs(self.warmup_bars - 1)
        prev_lows = ctx.lows(self.warmup_bars - 1)
        prev_closes = ctx.closes(self.warmup_bars - 1)
        prev_volumes = ctx.volumes(self.warmup_bars - 1)

        prev_atr = self._atr(prev_highs, prev_lows, prev_closes, self.atr_period)
        prev_avg_atr = self._atr(prev_highs[:-1], prev_lows[:-1], prev_closes[:-1], self.atr_period)
        prev_avg_volume = self._sma(prev_volumes[:-1], self.volume_period)

        if prev_atr is None or prev_avg_atr is None or prev_avg_volume is None or prev_avg_volume == 0:
            return None

        prev_bar_range = highs[-2] - lows[-2]
        prev_bar_volume = volumes[-2]

        is_prev_bar_range_climax = prev_bar_range > (prev_avg_atr * self.range_climax_multiplier)
        is_prev_bar_volume_climax = prev_bar_volume > (prev_avg_volume * self.volume_climax_multiplier)

        # Long Entry: Previous bar was a bearish climax, current bar shows reversal (fade)
        if (is_prev_bar_range_climax or is_prev_bar_volume_climax) and closes[-2] < opens[-2]: # Previous bar was bearish climax
            # Current bar opens lower or near previous close, then reverses upwards
            if ctx.bar.close > closes[-2] and (ctx.bar.close - ctx.bar.low) / ctx.bar.low > self.fade_reversal_pct:
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "bearish_climax_fade_long",
                            "prev_bar_range_climax": is_prev_bar_range_climax,
                            "prev_bar_volume_climax": is_prev_bar_volume_climax,
                            "prev_bar_range": prev_bar_range,
                            "avg_atr": avg_atr,
                            "prev_bar_volume": prev_bar_volume,
                            "avg_volume": avg_volume,
                            "current_close": ctx.bar.close,
                            "prev_close": closes[-2],
                            "fade_reversal_pct": (ctx.bar.close - ctx.bar.low) / ctx.bar.low
                        }
                    )

        # Short Entry: Previous bar was a bullish climax, current bar shows reversal (fade)
        if (is_prev_bar_range_climax or is_prev_bar_volume_climax) and closes[-2] > opens[-2]: # Previous bar was bullish climax
            # Current bar opens higher or near previous close, then reverses downwards
            if ctx.bar.close < closes[-2] and (ctx.bar.high - ctx.bar.close) / ctx.bar.high > self.fade_reversal_pct:
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "bullish_climax_fade_short",
                            "prev_bar_range_climax": is_prev_bar_range_climax,
                            "prev_bar_volume_climax": is_prev_bar_volume_climax,
                            "prev_bar_range": prev_bar_range,
                            "avg_atr": avg_atr,
                            "prev_bar_volume": prev_bar_volume,
                            "avg_volume": avg_volume,
                            "current_close": ctx.bar.close,
                            "prev_close": closes[-2],
                            "fade_reversal_pct": (ctx.bar.high - ctx.bar.close) / ctx.bar.high
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy on close.
        # Cooldown is handled in on_bar.
        pass