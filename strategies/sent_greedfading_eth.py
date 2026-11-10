from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFadeRally(Strategy):
    METADATA = {
        "name": "Sentiment Fade Rally",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2-hour avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 9
        self.slow_ema_period = 21
        self.rsi_period = 14
        self.rsi_overbought = 70
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        current_price = ctx.bar.close
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Entry logic: Fade rallies when Extreme Greed and momentum is losing steam (EMA bearish cross, RSI overbought)
        if not ctx.has_position():
            # Condition 1: Market sentiment is "Extreme Greed"
            is_extreme_greed = fear_greed_index >= 75

            # Condition 2: Momentum losing persistence (fast EMA crosses below slow EMA)
            # Check for a recent cross-down (current fast_ema < slow_ema, previous fast_ema > slow_ema)
            # And also ensure price is still relatively high (RSI overbought)
            if len(closes) > self.slow_ema_period + 1:
                prev_fast_ema = self._ema(closes[:-1], self.fast_ema_period)
                prev_slow_ema = self._ema(closes[:-1], self.slow_ema_period)

                if prev_fast_ema is not None and prev_slow_ema is not None:
                    ema_bearish_cross = (fast_ema < slow_ema and prev_fast_ema >= prev_slow_ema)

                    # Condition 3: RSI is overbought, indicating a stretched rally
                    is_rsi_overbought = rsi >= self.rsi_overbought

                    if is_extreme_greed and ema_bearish_cross and is_rsi_overbought:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            metadata={
                                "reason": "extreme_greed_ema_bearish_cross_rsi_overbought_fade",
                                "fear_greed_index": fear_greed_index,
                                "fast_ema": fast_ema,
                                "slow_ema": slow_ema,
                                "rsi": rsi,
                                "price": current_price,
                            }
                        )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust future sizing
        pass