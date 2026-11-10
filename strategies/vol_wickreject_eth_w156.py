from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRejectionFade(Strategy):
    METADATA = {
        "name": "Volatility Rejection Fade ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, target 1-3 bars hold
        "warmup_bars": 50, # Needs enough bars for ATR and SMA
        "required_features": [], # No external features needed for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.sma_period = 20
        self.cooldown_bars = 5 # Cooldown after a trade closes
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_volume = ctx.bar.volume

        prev_close = closes[-2]
        prev_open = opens[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]
        prev_volume = volumes[-2]

        # Calculate indicators
        atr = self._atr(highs, lows, closes, self.atr_period)
        sma_vol = self._sma(volumes, self.sma_period)
        sma_price = self._sma(closes, self.sma_period)

        if atr is None or sma_vol is None or sma_price is None:
            return None

        # Volatility Rejection Fade Logic
        # Look for large wicks (rejection) followed by confirmation
        
        # Long entry: Large lower wick (rejection of lower prices) followed by bullish confirmation
        # Condition 1: Previous bar had a significant lower wick (rejection from below)
        # Lower wick size relative to ATR and total bar range
        prev_lower_wick = prev_close - prev_low if prev_close > prev_open else prev_open - prev_low
        prev_bar_range = prev_high - prev_low

        # Ensure significant wick relative to ATR and total bar size
        is_significant_lower_wick = (prev_lower_wick > 1.5 * atr) and (prev_lower_wick > 0.4 * prev_bar_range)

        # Condition 2: Previous bar closed near its high (or above open if prev was red)
        # And current bar confirms with a bullish candle
        is_prev_bar_bullish_rejection_close = (prev_close > prev_open and (prev_high - prev_close) < (prev_close - prev_low)) or \
                                              (prev_close < prev_open and (prev_close > (prev_open + prev_low) / 2)) # Red bar, but closed in upper half

        is_current_bar_bullish_confirmation = (current_close > current_open) and (current_close > prev_close)

        # Condition 3: High volume on rejection bar
        is_high_volume_rejection = prev_volume > 1.5 * sma_vol

        # Condition 4: Price is below SMA (fading a bounce in a downtrend/consolidation)
        is_below_sma = current_close < sma_price

        if not ctx.has_position():
            if is_significant_lower_wick and is_prev_bar_bullish_rejection_close and \
               is_current_bar_bullish_confirmation and is_high_volume_rejection and is_below_sma:
                
                confidence = min(1.0, (prev_lower_wick / atr) * 0.2 + (current_volume / sma_vol) * 0.2) # Scale confidence
                confidence = max(0.4, min(0.9, confidence)) # Clamp confidence
                
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("long", confidence=confidence, metadata={
                    "reason": "lower_wick_rejection_fade_long",
                    "prev_lower_wick": prev_lower_wick,
                    "prev_atr": atr,
                    "prev_close": prev_close,
                    "prev_open": prev_open,
                    "current_close": current_close,
                    "current_open": current_open,
                    "prev_volume": prev_volume,
                    "sma_vol": sma_vol,
                    "current_price_vs_sma": current_close - sma_price
                })

            # Short entry: Large upper wick (rejection of higher prices) followed by bearish confirmation
            prev_upper_wick = prev_high - prev_close if prev_close < prev_open else prev_high - prev_open
            is_significant_upper_wick = (prev_upper_wick > 1.5 * atr) and (prev_upper_wick > 0.4 * prev_bar_range)

            is_prev_bar_bearish_rejection_close = (prev_close < prev_open and (prev_close - prev_low) < (prev_high - prev_close)) or \
                                                  (prev_close > prev_open and (prev_close < (prev_high + prev_open) / 2)) # Green bar, but closed in lower half

            is_current_bar_bearish_confirmation = (current_close < current_open) and (current_close < prev_close)

            # Condition 4: Price is above SMA (fading a drop in an uptrend/consolidation)
            is_above_sma = current_close > sma_price

            if is_significant_upper_wick and is_prev_bar_bearish_rejection_close and \
               is_current_bar_bearish_confirmation and is_high_volume_rejection and is_above_sma:

                confidence = min(1.0, (prev_upper_wick / atr) * 0.2 + (current_volume / sma_vol) * 0.2) # Scale confidence
                confidence = max(0.4, min(0.9, confidence)) # Clamp confidence

                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("short", confidence=confidence, metadata={
                    "reason": "upper_wick_rejection_fade_short",
                    "prev_upper_wick": prev_upper_wick,
                    "prev_atr": atr,
                    "prev_close": prev_close,
                    "prev_open": prev_open,
                    "current_close": current_close,
                    "current_open": current_open,
                    "prev_volume": prev_volume,
                    "sma_vol": sma_vol,
                    "current_price_vs_sma": current_close - sma_price
                })
        
        # No signal, hold current position or stay flat
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, if needed for adaptive logic.
        # For this strategy, cooldown is handled in on_bar based on last_trade_bar_index.
        pass