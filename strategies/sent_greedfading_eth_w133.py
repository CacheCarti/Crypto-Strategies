from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFadeRally(Strategy):
    METADATA = {
        "name": "Sentiment Fade Rally",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # Approx 2 hours
        "warmup_bars": 60, # For 50-period EMA and Z-score
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 50
        self.zscore_period = 20
        self.max_zscore_entry = 1.0 # Max Z-score to consider for fading
        self.min_fear_greed_for_short = 70 # Extreme greed starts around 75+
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + 1) # Need one more for Z-score calculation
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        ema = self._ema(closes, self.ema_period)
        zscore = self._zscore(closes, self.zscore_period)
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        if ema is None or zscore is None:
            return None

        # Fading rallies logic:
        # 1. Market is in "Extreme Greed" or "Greed" sentiment
        # 2. Price is above the EMA (uptrend)
        # 3. Z-score is low, indicating momentum is losing steam or consolidating after a run
        # 4. We are not currently in a position
        if not ctx.has_position():
            if (fear_greed_index >= self.min_fear_greed_for_short and
                current_price > ema and
                zscore < self.max_zscore_entry and
                zscore > -self.max_zscore_entry): # Ensure not too oversold either
                
                # Confidence scales with how "greedier" the market is and how much zscore is near zero
                confidence = min(0.9, (fear_greed_index - self.min_fear_greed_for_short) / 20.0 + (1.0 - abs(zscore)))
                confidence = max(0.4, confidence) # Minimum confidence

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_fade_rally",
                        "fear_greed_index": fear_greed_index,
                        "current_price": current_price,
                        "ema_long": ema,
                        "zscore_short_term": zscore,
                        "zscore_period": self.zscore_period,
                        "ema_period": self.ema_period,
                    }
                )
        elif ctx.position_direction() == "short":
            # If we are in a short position and fear_greed_index drops significantly
            # or market regime changes to crisis, consider exiting early.
            # This is an example of adaptive logic for exits.
            if fear_greed_index < self.min_fear_greed_for_short - 10: # Fear/Neutral territory
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "fear_greed_index_dropped_exit",
                        "current_fear_greed": fear_greed_index,
                        "entry_fear_greed": ctx.position.get("metadata", {}).get("fear_greed_index"),
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic based on position close for this strategy,
        # but could be used to adjust parameters for future trades.
        pass