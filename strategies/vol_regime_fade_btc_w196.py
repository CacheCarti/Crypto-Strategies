from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityConditionedFade(Strategy):
    METADATA = {
        "name": "Volatility Conditioned Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours
        "warmup_bars": 100,  # Z-score and ATR need sufficient history
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 60
        self.volatility_period = 60
        self.zscore_band = 2.0  # Entry threshold for z-score
        self.volatility_threshold_percentile = 0.75 # Top 25% of historical volatility
        self.last_signal_bar_index = -self.warmup_bars # Allow immediate signal after warmup
        self.cooldown_bars = 5 # Cooldown period after a signal

        # Keep track of past volatility for percentile calculation
        self.volatility_history = []
        self.max_volatility_history_size = 500 # Keep a reasonable history for percentile

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
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate Z-score
        current_zscore = self._zscore(closes, self.zscore_period)
        if current_zscore is None:
            return None

        # Calculate Volatility (using ATR for simplicity)
        current_atr = self._atr(highs, lows, closes, self.volatility_period)
        if current_atr is None:
            return None

        # Update volatility history
        self.volatility_history.append(current_atr)
        if len(self.volatility_history) > self.max_volatility_history_size:
            self.volatility_history.pop(0)

        # Determine volatility threshold
        if len(self.volatility_history) < self.volatility_period: # Need enough history to calculate percentile
            return None

        sorted_volatility = sorted(self.volatility_history)
        volatility_threshold_index = int(len(sorted_volatility) * self.volatility_threshold_percentile)
        min_high_volatility = sorted_volatility[volatility_threshold_index]

        is_high_volatility = current_atr > min_high_volatility

        # Market Context Filters
        current_fear_greed = ctx.features.get("fear_greed_index", 50)
        current_funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Position management
        if ctx.has_position():
            if ctx.position_direction() == "long" and current_zscore > -0.5: # Z-score normalized
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "zscore_normalization_exit",
                    "current_zscore": current_zscore,
                    "entry_zscore": ctx.position["metadata"].get("entry_zscore"),
                    "current_atr": current_atr,
                    "is_high_volatility": is_high_volatility,
                    "fear_greed": current_fear_greed,
                })
            elif ctx.position_direction() == "short" and current_zscore < 0.5: # Z-score normalized
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "zscore_normalization_exit",
                    "current_zscore": current_zscore,
                    "entry_zscore": ctx.position["metadata"].get("entry_zscore"),
                    "current_atr": current_atr,
                    "is_high_volatility": is_high_volatility,
                    "fear_greed": current_fear_greed,
                })
            return None # Hold position if not normalized

        # Entry Logic
        signal_direction = None
        confidence = 0.0
        reason = ""

        # Fade overbought condition
        if current_zscore > self.zscore_band and is_high_volatility:
            # Additional filter: avoid shorting into extreme fear (potential capitulation bounce)
            if current_fear_greed > 30: # Only short if not in extreme fear
                signal_direction = "short"
                confidence = min(0.6 + abs(current_zscore - self.zscore_band) * 0.1, 0.9)
                reason = "vol_cond_fade_overbought"
        # Fade oversold condition
        elif current_zscore < -self.zscore_band and is_high_volatility:
            # Additional filter: avoid longing into extreme greed (potential blow-off top)
            if current_fear_greed < 70: # Only long if not in extreme greed
                signal_direction = "long"
                confidence = min(0.6 + abs(current_zscore + self.zscore_band) * 0.1, 0.9)
                reason = "vol_cond_fade_oversold"

        if signal_direction:
            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_zscore": current_zscore,
                    "zscore_band": self.zscore_band,
                    "current_atr": current_atr,
                    "min_high_volatility_threshold": min_high_volatility,
                    "is_high_volatility": is_high_volatility,
                    "fear_greed": current_fear_greed,
                    "funding_rate": current_funding_rate,
                    "entry_zscore": current_zscore, # Store for exit logic
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: You could adjust parameters or confidence based on recent performance
        # For this strategy, we mainly use it for logging or future adaptive sizing.
        pass