from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class SentimentRecoveryETH(Strategy):
    METADATA = {
        "name": "SentimentRecoveryETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 20
        self.rsi_period = 14
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars  # Initialize to allow immediate trade
        self.last_fear_greed_value = 50  # Default neutral
        self.fear_greed_history = collections.deque(maxlen=5) # To detect transitions

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period):
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
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        # Fetch sentiment data
        current_fear_greed = ctx.features.get("fear_greed_index", 50)
        self.fear_greed_history.append(current_fear_greed)

        # Sentiment Recovery Logic
        # We want to enter when fear starts to recover from extreme levels.
        # This means the index was very low, and now it's increasing.
        fear_recovery_signal = False
        if len(self.fear_greed_history) >= 3:
            # Check if it was in extreme fear (e.g., < 20) and now it's rising
            # and not yet out of the 'fear' zone (e.g., still below 40)
            if (self.fear_greed_history[-3] < 20 and
                self.fear_greed_history[-2] < self.fear_greed_history[-1] and
                self.fear_greed_history[-1] > self.fear_greed_history[-2] and
                self.fear_greed_history[-1] < 40): # Still in fear/neutral zone
                fear_recovery_signal = True

        # Trading Logic
        if not ctx.has_position():
            # Long entry conditions
            if (fear_recovery_signal and
                fast_ema > slow_ema and  # Bullish EMA crossover/trend
                rsi < 50): # Not overbought, potentially still room to run

                confidence = 0.6 + (current_fear_greed / 100 * 0.2) # Scale confidence
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=confidence,
                                  metadata={
                                      "reason": "fear_recovery_ema_cross_rsi_momentum",
                                      "fear_greed_index": current_fear_greed,
                                      "fast_ema": fast_ema,
                                      "slow_ema": slow_ema,
                                      "rsi": rsi,
                                      "price": current_price
                                  })

            # Short entry conditions (opposite logic for completeness, though strategy is sentiment-recovery bullish)
            # This part is less emphasized by the prompt, focusing on fear recovery.
            # We will make it conservative.
            if (self.fear_greed_history and
                self.fear_greed_history[-1] > 70 and # Extreme greed
                self.last_fear_greed_value > self.fear_greed_history[-1] and # Greed starting to decline
                fast_ema < slow_ema and # Bearish EMA cross
                rsi > 50): # Not oversold, room to fall

                confidence = 0.5 + ((100 - current_fear_greed) / 100 * 0.2)
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=confidence,
                                  metadata={
                                      "reason": "greed_peak_ema_cross_rsi_momentum",
                                      "fear_greed_index": current_fear_greed,
                                      "fast_ema": fast_ema,
                                      "slow_ema": slow_ema,
                                      "rsi": rsi,
                                      "price": current_price
                                  })

        else: # Already in a position
            # Exit conditions (can be simplified if declared_sl_bps/tp_bps handle most exits)
            if ctx.position_direction() == "long":
                # Exit if sentiment turns negative or EMAs cross bearishly, or RSI overbought
                if (current_fear_greed < 20 or # Sentiment worsening
                    fast_ema < slow_ema or
                    rsi > 80):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7,
                                      metadata={
                                          "reason": "long_exit_sentiment_worsening_or_ema_cross_or_rsi_overbought",
                                          "fear_greed_index": current_fear_greed,
                                          "fast_ema": fast_ema,
                                          "slow_ema": slow_ema,
                                          "rsi": rsi,
                                          "price": current_price
                                      })
            elif ctx.position_direction() == "short":
                # Exit if sentiment improves or EMAs cross bullishly, or RSI oversold
                if (current_fear_greed > 50 or # Sentiment improving
                    fast_ema > slow_ema or
                    rsi < 20):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7,
                                      metadata={
                                          "reason": "short_exit_sentiment_improving_or_ema_cross_or_rsi_oversold",
                                          "fear_greed_index": current_fear_greed,
                                          "fast_ema": fast_ema,
                                          "slow_ema": slow_ema,
                                          "rsi": rsi,
                                          "price": current_price
                                      })

        self.last_fear_greed_value = current_fear_greed
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive logic
        pass