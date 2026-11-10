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
        self.fear_period = 5 # Period to check for fear recovery
        self.ema_period = 20 # EMA for price confirmation
        self.rsi_period = 14 # RSI for momentum confirmation
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.cooldown_bars * 2 # Initialize to allow first trade

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
            return 100.0 if avg_gain > 0 else 50.0 # Handle flat market or only gains
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + self.rsi_period)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_fear_greed = ctx.features.get("fear_greed_index", 50)
        
        # We need recent fear_greed history to detect recovery
        # Since ctx.features.get only gives current, we assume a simple recovery check
        # This is a simplification as we don't have historical feature values directly
        # For simplicity, we check if current fear is low but not "extreme" and was lower recently
        
        # Simplified sentiment recovery: current fear is low-moderate, indicating recovery from extreme fear
        # Assume "extreme fear" is < 20, "fear" is 20-40. Recovery is when it moves from <20 to 20-30.
        is_fear_recovering = False
        if 20 <= current_fear_greed <= 35: # Current state is fearful but not extreme
            # This is a proxy for "recovering from extreme". In a real system, we'd need historical F&G.
            # Here, we assume if it's in this range, it might be recovering.
            is_fear_recovering = True

        if not is_fear_recovering:
            if ctx.has_position():
                # Exit if sentiment is no longer in recovery zone and we are long
                if ctx.position_direction() == "long" and current_fear_greed > 45: # Sentiment improved or worsened too much
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "sentiment_no_longer_recovering", "fear_greed": current_fear_greed})
            return None # No signal if not in fear recovery state
        
        ema = self._ema(closes, self.ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema is None or rsi is None:
            return None

        current_price = ctx.bar.close

        # Long entry conditions:
        # 1. Sentiment is in a "recovery from fear" zone
        # 2. Price is above EMA (uptrend confirmation)
        # 3. RSI is not overbought (avoid chasing)
        if not ctx.has_position() and is_fear_recovering:
            if current_price > ema and rsi < 60: # Not overbought
                confidence = 0.6 + (1 - (current_fear_greed / 50)) * 0.2 # Higher confidence if more fearful
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=min(0.9, confidence),
                    metadata={
                        "reason": "sentiment_fear_recovery_ema_support_rsi_not_overbought",
                        "fear_greed": current_fear_greed,
                        "price": current_price,
                        "ema": ema,
                        "rsi": rsi
                    }
                )
        
        # Exit logic for existing position
        if ctx.has_position() and ctx.position_direction() == "long":
            # Exit if RSI becomes overbought or sentiment turns greedy
            if rsi > 70 or current_fear_greed > 60:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_exit_rsi_overbought_or_sentiment_greedy",
                        "fear_greed": current_fear_greed,
                        "price": current_price,
                        "rsi": rsi
                    }
                )
            
            # Exit if price drops below EMA (trend breakdown)
            if current_price < ema:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "long_exit_price_below_ema",
                        "fear_greed": current_fear_greed,
                        "price": current_price,
                        "ema": ema
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just reset last trade bar to enforce cooldown
        self.last_trade_bar = ctx.bar_index
        pass