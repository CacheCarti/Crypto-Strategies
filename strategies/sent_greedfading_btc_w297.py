from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedFade(Strategy):
    METADATA = {
        "name": "Fear Greed Fade BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-hour avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
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
        
        # Calculate initial average gain/loss
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

        if len(closes) < self.slow_ema_period + 1 or len(highs) < self.rsi_period + 1 or len(lows) < self.rsi_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        
        # Condition 1: Extreme Greed (Sentiment Filter)
        is_extreme_greed = fear_greed_index >= 75 # Threshold for "Extreme Greed"

        # Condition 2: Momentum Loss/Reversal (OHLCV Trigger)
        # We look for a bearish EMA crossover AND overbought RSI
        is_bearish_momentum_loss = (fast_ema < slow_ema and closes[-2] > closes[-1] and rsi > 70)
        
        if not ctx.has_position():
            # Entry logic: Fade rallies when in extreme greed and momentum is losing steam (bearish cross + overbought RSI)
            if is_extreme_greed and is_bearish_momentum_loss:
                # Confidence scales with how deep into extreme greed we are
                confidence = min(1.0, (fear_greed_index - 75) / 25.0 + 0.5) 
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "extreme_greed_fade_bearish_ema_cross_rsi_overbought",
                        "fear_greed_index": fear_greed_index,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": rsi,
                        "current_price": current_price
                    }
                )
        else: # Already in a position
            # If we are short and conditions turn bullish, consider exiting
            if ctx.position_direction() == "short":
                # Exit if fast EMA crosses above slow EMA OR RSI becomes oversold (potential bounce)
                if fast_ema > slow_ema or rsi < 30:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "exit_short_ema_cross_or_rsi_oversold",
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "rsi": rsi,
                            "current_price": current_price
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy, but good for logging
        pass