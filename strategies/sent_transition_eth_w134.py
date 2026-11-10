from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentRecoveryStrategy(Strategy):
    METADATA = {
        "name": "Sentiment Recovery Strategy",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 7
        self.cooldown_bars = 5 # Wait 5 bars after a trade before entering again
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow trade on first bar

        # Sentiment thresholds for fear recovery
        self.extreme_fear_threshold = 20
        self.fear_threshold = 35
        self.neutral_threshold = 50

        # State variables for sentiment tracking
        self.prev_fear_greed_index: Optional[float] = None
        self.is_in_extreme_fear_recovery: bool = False

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
        closes = ctx.closes(self.slow_ema_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        current_rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or current_rsi is None:
            return None

        # Get Fear & Greed Index
        fear_greed_index = ctx.features.get("fear_greed_index", 50.0)

        # Update sentiment recovery state
        if self.prev_fear_greed_index is not None:
            if fear_greed_index <= self.extreme_fear_threshold and self.prev_fear_greed_index > self.extreme_fear_threshold:
                # Entered extreme fear
                self.is_in_extreme_fear_recovery = False # Reset flag

            elif self.prev_fear_greed_index <= self.extreme_fear_threshold and fear_greed_index > self.extreme_fear_threshold:
                # Started recovering from extreme fear
                self.is_in_extreme_fear_recovery = True

            elif fear_greed_index >= self.neutral_threshold:
                # Sentiment has reached neutral or greed, recovery is over
                self.is_in_extreme_fear_recovery = False

        self.prev_fear_greed_index = fear_greed_index

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit logic
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # If sentiment recovery ends or turns negative, consider exiting long
                if not self.is_in_extreme_fear_recovery and fear_greed_index < self.neutral_threshold:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "sentiment_recovery_ended_long_exit",
                        "fear_greed_index": fear_greed_index,
                        "current_price": current_price
                    })
                # Or if price action shows weakness during recovery
                if current_rsi > 70: # Overbought during recovery, might be a good exit point
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "rsi_overbought_long_exit_during_recovery",
                        "rsi": current_rsi,
                        "fear_greed_index": fear_greed_index,
                        "current_price": current_price
                    })
            # No short positions in this strategy, so no specific short exit logic needed

            return None # Hold position if no exit condition met

        # Entry logic (only if not in a position)
        if not ctx.has_position():
            # Long entry: price-based trigger during sentiment recovery
            if self.is_in_extreme_fear_recovery:
                # Look for a bullish cross and oversold RSI as a trigger
                if fast_ema > slow_ema and closes[-2] <= slow_ema and current_rsi < 40:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("long", confidence=0.8, metadata={
                        "reason": "sentiment_recovery_ema_cross_rsi_oversold",
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": current_rsi,
                        "fear_greed_index": fear_greed_index,
                        "current_price": current_price
                    })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis, e.g., print(position)
        pass