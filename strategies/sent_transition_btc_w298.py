from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedRecovery(Strategy):
    METADATA = {
        "name": "Fear Greed Recovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.cooldown_bars = 5 # Wait 5 bars after a trade before entering again
        self.last_signal_bar = -self.cooldown_bars

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
            return 100.0
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

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        prev_fear_greed_index = ctx.features.get("fear_greed_index", 50, -1) # Get previous bar's F&G

        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        current_price = ctx.bar.close

        # Long signal:
        # 1. Fear & Greed index was in "Extreme Fear" (or "Fear") and is now recovering (increasing).
        # 2. Fast EMA crosses above Slow EMA (bullish momentum).
        # 3. RSI is not overbought (below 70).
        if not ctx.has_position():
            if (prev_fear_greed_index <= 30 and fear_greed_index > prev_fear_greed_index and fear_greed_index > 20 and fear_greed_index <= 40) and \
               (fast_ema > slow_ema and ctx.closes(2)[-2] < ctx.opens(2)[-2] and ctx.bar.close > slow_ema) and \
               (rsi < 70):
                
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "fear_greed_recovery_ema_cross_rsi_ok",
                        "fear_greed_index": fear_greed_index,
                        "prev_fear_greed_index": prev_fear_greed_index,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": rsi,
                        "price": current_price
                    }
                )

        # Short signal:
        # 1. Fear & Greed index was in "Extreme Greed" (or "Greed") and is now declining (decreasing).
        # 2. Fast EMA crosses below Slow EMA (bearish momentum).
        # 3. RSI is not oversold (above 30).
        elif not ctx.has_position():
            if (prev_fear_greed_index >= 70 and fear_greed_index < prev_fear_greed_index and fear_greed_index < 80 and fear_greed_index >= 60) and \
               (fast_ema < slow_ema and ctx.closes(2)[-2] > ctx.opens(2)[-2] and ctx.bar.close < slow_ema) and \
               (rsi > 30):
                
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "greed_decline_ema_cross_rsi_ok",
                        "fear_greed_index": fear_greed_index,
                        "prev_fear_greed_index": prev_fear_greed_index,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": rsi,
                        "price": current_price
                    }
                )
        
        # Exit logic (can be combined with other conditions or just use SL/TP)
        if ctx.has_position():
            # If the market regime turns volatile or crisis, consider exiting early
            if ctx.market.get("regime", "NORMAL") in ["VOLATILE", "CRISIS"]:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "market_regime_shift_exit",
                        "current_regime": ctx.market.get("regime"),
                        "fear_greed_index": fear_greed_index,
                        "price": current_price
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis or reset state if needed
        pass