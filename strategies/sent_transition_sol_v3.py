from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentRecoverySOL(Strategy):
    METADATA = {
        "name": "Sentiment Recovery SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.cooldown_bars = 10 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Sentiment thresholds
        self.extreme_fear_threshold = 20
        self.fear_recovery_threshold = 30
        self.neutral_threshold = 45

        self.last_fear_greed_index = None
        self.in_fear_recovery_state = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        # Calculate initial SMA for the first EMA
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
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        current_rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or current_rsi is None:
            return None

        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Update fear recovery state
        if fear_greed_index <= self.extreme_fear_threshold:
            # We are in extreme fear, potential for recovery
            self.in_fear_recovery_state = True
        elif self.in_fear_recovery_state and fear_greed_index > self.fear_recovery_threshold:
            # Fear is recovering, can look for entry
            self.in_fear_recovery_state = False # Reset state after recovery detected
        elif fear_greed_index >= self.neutral_threshold:
            # Market is neutral or greedy, not in fear recovery
            self.in_fear_recovery_state = False

        # Entry logic
        if not ctx.has_position():
            # Long entry: Fear recovery + bullish price action
            if self.in_fear_recovery_state and \
               fear_greed_index > self.last_fear_greed_index and \
               fear_greed_index > self.extreme_fear_threshold and \
               fast_ema > slow_ema and \
               current_rsi < 60 and \
               ctx.closes(2)[0] < ctx.closes(2)[1]: # Price rising
                
                self.last_trade_bar = ctx.bar_index
                self.in_fear_recovery_state = False # Trade fired, reset state
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "fear_recovery_ema_cross_rsi_momentum",
                        "fear_greed_index": fear_greed_index,
                        "last_fear_greed_index": self.last_fear_greed_index,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": current_rsi,
                        "close": current_close,
                    }
                )

        # Exit logic (if position open)
        if ctx.has_position():
            # If current fear_greed_index drops significantly or becomes too high
            if fear_greed_index < self.extreme_fear_threshold or \
               fear_greed_index > 70: # Exit if sentiment turns bad again or gets too euphoric
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "sentiment_regime_shift_exit",
                        "fear_greed_index": fear_greed_index,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": current_rsi,
                        "close": current_close,
                    }
                )
            
            # Or if price action turns bearish (EMA crossover back)
            if fast_ema < slow_ema and ctx.position_direction() == "long":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "ema_bearish_cross_exit",
                        "fear_greed_index": fear_greed_index,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": current_rsi,
                        "close": current_close,
                    }
                )

        self.last_fear_greed_index = fear_greed_index
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: You can use this to log or adapt behavior after a trade closes.
        # For this strategy, we already handle cooldown in on_bar.
        pass