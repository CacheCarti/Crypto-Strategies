from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class VolatilityConditionedFade(Strategy):
    METADATA = {
        "name": "Volatility Conditioned Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 3,  # Target 3-hour hold
        "warmup_bars": 60,  # Need enough bars for Z-score and volatility
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 20
        self.volatility_period = 20
        self.zscore_band_multiplier = 2.5
        self.volatility_quantile_threshold = 0.75  # Top 25% of volatility
        self.min_fear_greed = 20 # Extreme Fear
        self.max_fear_greed = 80 # Extreme Greed
        self.cooldown_bars = 5
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

    def _realized_volatility(self, closes, period):
        if len(closes) < period + 1:
            return None
        log_returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes[-period-1:]))]
        if len(log_returns) < period:
            return None
        return statistics.stdev(log_returns) * math.sqrt(252 * 24) # Annualized hourly vol

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown period check
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate Z-score
        z_score = self._zscore(closes, self.zscore_period)
        if z_score is None:
            return None

        mean = self._sma(closes, self.zscore_period)
        std = self._stddev(closes, self.zscore_period)
        if mean is None or std is None or std == 0:
            return None

        upper_band = mean + self.zscore_band_multiplier * std
        lower_band = mean - self.zscore_band_multiplier * std

        # Calculate Realized Volatility
        current_vol = self._realized_volatility(closes, self.volatility_period)
        if current_vol is None:
            return None

        # Store recent volatilities to determine quantile threshold (simple approximation)
        # In a real system, this would be a longer history or a more sophisticated quantile.
        # For dMoERA, we keep it simple to fit within constraints.
        if not hasattr(self, 'recent_vols'):
            self.recent_vols = []
        self.recent_vols.append(current_vol)
        if len(self.recent_vols) > 100: # Keep a rolling window of recent vols
            self.recent_vols.pop(0)

        # Determine if current volatility is in the top quartile of recent volatilities
        if len(self.recent_vols) < self.volatility_period: # Need enough history for quantile
            return None

        sorted_vols = sorted(self.recent_vols)
        volatility_threshold_value = sorted_vols[int(len(sorted_vols) * self.volatility_quantile_threshold)]
        is_high_volatility = current_vol >= volatility_threshold_value

        # Get Fear & Greed Index
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # --- Entry Logic ---
        if not ctx.has_position():
            # Fade Long (Short Entry): Price is significantly above the mean (high Z-score)
            # and market is overbought (Greed) AND high volatility
            if current_price > upper_band and is_high_volatility and fear_greed_index >= self.max_fear_greed:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "fade_long_high_vol_zscore_overbought",
                        "z_score": z_score,
                        "current_price": current_price,
                        "upper_band": upper_band,
                        "realized_volatility": current_vol,
                        "volatility_threshold": volatility_threshold_value,
                        "fear_greed_index": fear_greed_index,
                    }
                )

            # Fade Short (Long Entry): Price is significantly below the mean (low Z-score)
            # and market is oversold (Fear) AND high volatility
            elif current_price < lower_band and is_high_volatility and fear_greed_index <= self.min_fear_greed:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "fade_short_high_vol_zscore_oversold",
                        "z_score": z_score,
                        "current_price": current_price,
                        "lower_band": lower_band,
                        "realized_volatility": current_vol,
                        "volatility_threshold": volatility_threshold_value,
                        "fear_greed_index": fear_greed_index,
                    }
                )
        else:
            # --- Exit Logic (Z-score normalization) ---
            position_direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]

            if position_direction == "short":
                # Exit short if price comes back into the band or z_score normalizes
                if current_price < upper_band and z_score > -0.5: # Z-score normalized or moved significantly lower
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "short_exit_zscore_normalized",
                        "current_z_score": z_score,
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "upper_band": upper_band,
                    })
            elif position_direction == "long":
                # Exit long if price comes back into the band or z_score normalizes
                if current_price > lower_band and z_score < 0.5: # Z-score normalized or moved significantly higher
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "long_exit_zscore_normalized",
                        "current_z_score": z_score,
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "lower_band": lower_band,
                    })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust future sizing
        pass