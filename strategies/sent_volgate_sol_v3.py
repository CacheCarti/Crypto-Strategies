from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentVolGateSol(Strategy):
    METADATA = {
        "name": "Sentiment Volatility Gate SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.std_dev_multiplier = 1.0
        self.fear_greed_threshold_extreme = 25 # Extreme Fear/Greed
        self.fear_greed_threshold_normal = 45  # Normal Fear/Greed boundaries
        self.last_signal_bar = -1
        self.cooldown_bars = 5 # Cooldown period after a signal

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

    def _realized_volatility(self, closes, period):
        if len(closes) < period + 1:
            return None
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        return self._stddev(returns, period) * math.sqrt(252 * 24) # Annualize hourly vol

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.vol_period + 1)
        if len(closes) < self.vol_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Calculate realized volatility
        realized_vol = self._realized_volatility(closes, self.vol_period)
        if realized_vol is None:
            return None

        # Calculate average volatility for comparison
        all_realized_vols = [self._realized_volatility(ctx.closes(i+self.vol_period+1), self.vol_period)
                             for i in range(self.vol_period, ctx.bar_index - self.vol_period)]
        all_realized_vols = [v for v in all_realized_vols if v is not None]

        if not all_realized_vols: # Not enough history for avg vol
            return None
        
        avg_realized_vol = sum(all_realized_vols) / len(all_realized_vols)
        vol_deviation = (realized_vol - avg_realized_vol) / avg_realized_vol if avg_realized_vol != 0 else 0

        signal_direction = None
        confidence = 0.0
        reason = "no_signal"
        
        # Long condition: Extreme Fear + Elevated Volatility (panic)
        if fear_greed_index <= self.fear_greed_threshold_extreme and vol_deviation > self.std_dev_multiplier * 0.5: # 50% above avg vol
            if not ctx.has_position():
                signal_direction = "long"
                confidence = 0.7 + (ctx.rng.uniform(0, 1) * 0.1) # Some randomness
                reason = "extreme_fear_elevated_vol_panic_buy"
        
        # Short condition: Extreme Greed + Elevated Volatility (euphoria)
        elif fear_greed_index >= (100 - self.fear_greed_threshold_extreme) and vol_deviation > self.std_dev_multiplier * 0.5:
            if not ctx.has_position():
                signal_direction = "short"
                confidence = 0.7 + (ctx.rng.uniform(0, 1) * 0.1)
                reason = "extreme_greed_elevated_vol_euphoria_sell"

        # Exit condition: Sentiment returns to normal or volatility subsides
        if ctx.has_position():
            if ctx.position_direction() == "long":
                if fear_greed_index > self.fear_greed_threshold_normal or vol_deviation < self.std_dev_multiplier * 0.2: # Volatility subsides
                    signal_direction = "flat"
                    confidence = 0.6
                    reason = "long_exit_sentiment_normal_or_vol_subsided"
            elif ctx.position_direction() == "short":
                if fear_greed_index < (100 - self.fear_greed_threshold_normal) or vol_deviation < self.std_dev_multiplier * 0.2:
                    signal_direction = "flat"
                    confidence = 0.6
                    reason = "short_exit_sentiment_normal_or_vol_subsided"

        if signal_direction:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=min(1.0, confidence),
                metadata={
                    "reason": reason,
                    "fear_greed_index": fear_greed_index,
                    "realized_volatility": realized_vol,
                    "avg_realized_vol": avg_realized_vol,
                    "vol_deviation": vol_deviation,
                    "current_price": current_price
                }
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # For logging or future adaptive sizing, not used in this version
        pass