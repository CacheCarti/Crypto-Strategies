from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFadeRally(Strategy):
    METADATA = {
        "name": "Sentiment Fade Rally (BTC)",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.cooldown_bars = 5 # Cooldown period after a trade

        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow trade immediately

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
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.slow_ema_period + 1)
        highs = ctx.highs(self.rsi_period + 1)
        lows = ctx.lows(self.rsi_period + 1)

        if len(closes) < self.slow_ema_period or len(highs) < self.rsi_period or len(lows) < self.rsi_period:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar <= self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Strategy: Fade rallies when sentiment is extreme greed and momentum is waning (EMA cross down, RSI overbought)
        # We are looking to short.
        if not ctx.has_position():
            # Condition 1: Extreme Greed Sentiment
            is_extreme_greed = fear_greed_index >= 75 # 75-100 is "Extreme Greed"

            # Condition 2: Price is overbought
            is_overbought_rsi = rsi > 70

            # Condition 3: Momentum is losing persistence (fast EMA crosses below slow EMA)
            # Check for a recent bearish crossover
            if len(closes) >= self.slow_ema_period + 1:
                # Calculate EMAs for the previous bar
                prev_closes = closes[:-1]
                prev_fast_ema = self._ema(prev_closes, self.fast_ema_period)
                prev_slow_ema = self._ema(prev_closes, self.slow_ema_period)

                if prev_fast_ema is not None and prev_slow_ema is not None:
                    ema_cross_down = (prev_fast_ema > prev_slow_ema) and (fast_ema < slow_ema)
                else:
                    ema_cross_down = False
            else:
                ema_cross_down = False

            if is_extreme_greed and is_overbought_rsi and ema_cross_down:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_fade_rally",
                        "fear_greed_index": fear_greed_index,
                        "rsi": rsi,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_price": current_price
                    }
                )
        elif ctx.position_direction() == "short":
            # If we are in a short position and sentiment drops significantly
            # or if price recovers above fast EMA, consider exiting.
            if fear_greed_index < 50 and rsi < 50: # Sentiment shifts from greed to neutral/fear and RSI cools down
                 return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "sentiment_shift_and_rsi_cooled",
                        "fear_greed_index": fear_greed_index,
                        "rsi": rsi,
                        "current_price": current_price
                    }
                )
            # Or if fast EMA crosses back above slow EMA, indicating trend reversal
            if fast_ema > slow_ema:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "bearish_momentum_reversal",
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_price": current_price
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar on close for cooldown
        self.last_trade_bar = ctx.bar_index
        # You could add adaptive logic here, e.g., adjust confidence based on recent performance
        pass