from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGreedFade(Strategy):
    METADATA = {
        "name": "SentimentGreedFade",
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
        self.rsi_period = 7 # Shorter RSI for momentum loss detection
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
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
        
        # Calculate initial average gain/loss
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 # No losses, RSI is 100
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
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

        # Regime Filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Entry Logic: Fade rallies when Extreme Greed and momentum loss
        if not ctx.has_position():
            # Condition 1: Extreme Greed sentiment
            is_extreme_greed = fear_greed_index >= 75 # Using 75 as threshold for extreme greed

            # Condition 2: Momentum loss (fast EMA crossing below slow EMA, or RSI showing weakness)
            # We want to SHORT when price is above EMAs, but momentum is fading
            is_momentum_loss = (fast_ema < slow_ema and closes[-1] < fast_ema) or (rsi > 70 and rsi < self._rsi(closes[:-1], self.rsi_period)) # RSI divergence/turn

            # Condition 3: Avoid trading in strong bullish trends or crisis
            is_unfavorable_regime = trend_regime == "bull" or regime == "CRISIS"

            if is_extreme_greed and is_momentum_loss and not is_unfavorable_regime:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "extreme_greed_momentum_fade",
                        "fear_greed_index": fear_greed_index,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": rsi,
                        "current_price": current_price,
                        "regime": regime,
                        "trend_regime": trend_regime,
                    }
                )
        
        # Exit Logic: Flat position if conditions reverse or profit/loss targets hit
        # The engine handles SL/TP automatically. This is for discretionary exits.
        if ctx.has_position() and ctx.position_direction() == "short":
            # If sentiment shifts from extreme greed, or momentum turns bullish, consider exiting
            if fear_greed_index < 70 or (fast_ema > slow_ema and closes[-1] > fast_ema):
                 self.last_trade_bar = ctx.bar_index
                 return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "short_exit_sentiment_or_momentum_shift",
                        "fear_greed_index": fear_greed_index,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": rsi,
                        "current_price": current_price,
                        "regime": regime,
                        "trend_regime": trend_regime,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt strategy based on performance
        # For simplicity, no adaptive logic implemented here, just logging.
        pass