from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFadeRally(Strategy):
    METADATA = {
        "name": "Sentiment Fade Rally BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "fear_greed_class"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.momentum_period = 5
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _momentum(self, closes, period):
        if len(closes) < period:
            return None
        return closes[-1] - closes[-period]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period)
        highs = ctx.highs(self.slow_ema_period)
        lows = ctx.lows(self.slow_ema_period)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Market context and features
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        fear_greed_class = ctx.features.get("fear_greed_class", "Neutral")

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        momentum = self._momentum(closes, self.momentum_period)

        if fast_ema is None or slow_ema is None or momentum is None:
            return None

        # Entry logic: Fade rallies when fear_greed_index is extreme-greed and momentum loses persistence
        if not ctx.has_position():
            # Condition 1: Extreme Greed sentiment
            is_extreme_greed = (fear_greed_class == "Extreme Greed" or fear_greed_index >= 80)

            # Condition 2: Price is above slow EMA (indicating a rally)
            is_above_slow_ema = closes[-1] > slow_ema

            # Condition 3: Momentum losing persistence (e.g., momentum turning negative after being positive)
            # We look for a recent peak in price followed by a drop in momentum
            # A simple way to check for 'losing persistence' is if current momentum is negative
            # after a period of positive momentum (implied by price above slow EMA).
            # Or, more directly: price has closed below the fast EMA after being above it,
            # indicating a short-term reversal or loss of upward thrust.
            momentum_reversal_signal = (
                closes[-1] < fast_ema and # current price below fast EMA
                closes[-2] >= fast_ema # previous price was above or at fast EMA
            )

            if is_extreme_greed and is_above_slow_ema and momentum_reversal_signal:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fade_rally_extreme_greed_momentum_reversal",
                        "fear_greed_index": fear_greed_index,
                        "fear_greed_class": fear_greed_class,
                        "current_price": closes[-1],
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "momentum": momentum,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive logic
        # For this strategy, no specific adaptive logic is implemented on close.
        pass