from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityFadeBTC(Strategy):
    METADATA = {
        "name": "Volatility Fade BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 3,  # Aim for a few hours hold
        "warmup_bars": 100,  # Z-score and ATR need a decent history
        "required_features": [], # No specific features from ctx.features.get() needed
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 50
        self.atr_period = 20
        self.zscore_threshold = 2.0  # Entry threshold for z-score
        self.volatility_threshold_multiplier = 1.2 # How much higher than avg ATR to consider high vol
        self.cooldown_bars = 10 # Bars to wait after a trade
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

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        current_zscore = self._zscore(closes, self.zscore_period)
        current_atr = self._atr(highs, lows, closes, self.atr_period)
        
        if current_zscore is None or current_atr is None:
            return None

        # Calculate average ATR to determine high volatility regime
        historical_atrs = [self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.atr_period) for i in range(self.atr_period, len(closes))]
        historical_atrs = [atr for atr in historical_atrs if atr is not None]
        
        if not historical_atrs:
            return None

        avg_atr = sum(historical_atrs) / len(historical_atrs)
        is_high_volatility = current_atr > avg_atr * self.volatility_threshold_multiplier

        # Get market trend regime
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Entry conditions
        if not ctx.has_position():
            # Long entry: price significantly below its rolling mean (negative z-score)
            # Only in high volatility and not in a strong bear trend
            if current_zscore < -self.zscore_threshold and is_high_volatility and trend_regime != "bear":
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=min(1.0, abs(current_zscore) / 3.0), # Confidence scales with z-score extremity
                    metadata={
                        "reason": "zscore_fade_long_high_vol",
                        "zscore": current_zscore,
                        "atr": current_atr,
                        "avg_atr": avg_atr,
                        "trend_regime": trend_regime,
                        "price": ctx.bar.close,
                    }
                )

            # Short entry: price significantly above its rolling mean (positive z-score)
            # Only in high volatility and not in a strong bull trend
            elif current_zscore > self.zscore_threshold and is_high_volatility and trend_regime != "bull":
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=min(1.0, current_zscore / 3.0), # Confidence scales with z-score extremity
                    metadata={
                        "reason": "zscore_fade_short_high_vol",
                        "zscore": current_zscore,
                        "atr": current_atr,
                        "avg_atr": avg_atr,
                        "trend_regime": trend_regime,
                        "price": ctx.bar.close,
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last signal bar on close to apply cooldown
        self.last_signal_bar = ctx.bar_index
        # You could also use this to adapt parameters based on performance
        pass