from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFadeRallyETH(Strategy):
    METADATA = {
        "name": "SentimentFadeRallyETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.oversold_rsi = 30 # Not used for this strategy
        self.overbought_rsi = 70
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

        # State to detect transitions
        self.last_fear_greed_class = None
        self.last_ema_cross_state = None # -1: bearish, 0: neutral, 1: bullish

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
            return 100.0 if avg_gain > 0 else 50.0 # Prevent division by zero
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        fear_greed_class = ctx.features.get("fear_greed_class", "Neutral")
        
        signal = None
        metadata = {}

        # Logic for shorting overbought conditions during extreme greed
        # Only consider shorting if no position is open or if we are currently long (to flip)
        if not ctx.has_position() or ctx.position_direction() == "long":
            # Condition 1: Extreme Greed sentiment
            is_extreme_greed = (fear_greed_class == "Extreme Greed" or fear_greed_index >= 75)

            # Condition 2: Price showing overbought signs (RSI)
            is_rsi_overbought = (rsi > self.overbought_rsi)

            # Condition 3: Momentum loss (fast EMA crosses below slow EMA, or is close and trending down)
            # We want to detect the *transition* of momentum loss
            current_ema_cross_state = 0
            if fast_ema > slow_ema:
                current_ema_cross_state = 1 # Bullish
            elif fast_ema < slow_ema:
                current_ema_cross_state = -1 # Bearish

            momentum_losing_persistence = False
            if self.last_ema_cross_state == 1 and current_ema_cross_state == -1: # Bullish to Bearish cross
                momentum_losing_persistence = True
            elif self.last_ema_cross_state == 1 and current_ema_cross_state == 0 and fast_ema < closes[-2]: # Bullish to neutral, price dropping
                momentum_losing_persistence = True
            elif current_ema_cross_state == 1 and fast_ema < closes[-2]: # Still bullish but fast EMA is turning down
                 momentum_losing_persistence = True


            # Entry signal: Extreme Greed + RSI Overbought + Momentum Loss transition
            if is_extreme_greed and is_rsi_overbought and momentum_losing_persistence:
                confidence = min(1.0, (fear_greed_index - 75) / 25.0 + (rsi - self.overbought_rsi) / 30.0 + 0.2)
                confidence = max(0.5, min(0.9, confidence)) # Ensure confidence is reasonable
                
                signal = ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "extreme_greed_rsi_overbought_momentum_loss",
                        "fear_greed_index": fear_greed_index,
                        "fear_greed_class": fear_greed_class,
                        "rsi": rsi,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_price": current_price,
                    }
                )
                self.last_signal_bar = ctx.bar_index

        # Update state for transition detection
        self.last_fear_greed_class = fear_greed_class
        if fast_ema > slow_ema:
            self.last_ema_cross_state = 1
        elif fast_ema < slow_ema:
            self.last_ema_cross_state = -1
        else:
            self.last_ema_cross_state = 0

        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown or log for adaptive sizing if needed
        pass