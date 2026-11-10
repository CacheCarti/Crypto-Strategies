from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedRecovery(Strategy):
    METADATA = {
        "name": "FearGreedRecovery",
        "domain": "btc_usdc",
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
        self.min_bars_since_trade = 5 # Cooldown period

        self.last_signal_bar = -self.min_bars_since_trade

        # Fear & Greed thresholds
        self.extreme_fear_threshold = 20
        self.recovery_threshold = 30

        # Confidence scaling
        self.base_confidence = 0.6
        self.max_confidence = 0.9

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

        # Cooldown check
        if ctx.bar_index < self.last_signal_bar + self.min_bars_since_trade:
            return None

        current_price = ctx.bar.close

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        # Market context for regime filtering
        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")

        # Fear & Greed Index
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        
        prev_fear_greed_index = ctx.features.get("fear_greed_index", 50, -1) # Get previous bar's FGI

        # Entry logic for LONG
        if not ctx.has_position():
            # Condition 1: Fear & Greed recovery (sentiment transition)
            # FGI was in extreme fear, and now it's starting to recover
            fgi_recovery = (prev_fear_greed_index <= self.extreme_fear_threshold and
                            fear_greed_index > self.extreme_fear_threshold and
                            fear_greed_index < self.recovery_threshold) # Not yet out of "fear" but improving
            
            # Condition 2: Price action confirmation (EMA crossover + RSI)
            ema_bull_cross = fast_ema > slow_ema and ctx.closes(2)[-2] < ctx.opens(2)[-2] # Only consider if previous bar was bullish
            rsi_oversold = rsi < 40 and rsi > 20 # Not extremely oversold, but in the lower range
            
            # Condition 3: Regime filter
            # Only trade long if not in a strong bear trend or crisis regime
            favorable_regime = (trend_regime != "bear" and regime != "CRISIS")

            if fgi_recovery and ema_bull_cross and rsi_oversold and favorable_regime:
                confidence = self.base_confidence + (fear_greed_index / 100.0) * 0.2 + (rsi / 100.0) * 0.1
                confidence = min(confidence, self.max_confidence) # Cap confidence

                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "fear_greed_recovery_ema_rsi_long",
                        "fear_greed_index": fear_greed_index,
                        "prev_fear_greed_index": prev_fear_greed_index,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": rsi,
                        "trend_regime": trend_regime,
                        "market_regime": regime,
                        "price": current_price
                    }
                )

        # Exit logic (if position exists)
        if ctx.has_position():
            # If FGI starts to become extreme greed or drops back into extreme fear while in a long position
            # This serves as a dynamic exit condition based on sentiment
            if ctx.position_direction() == "long":
                if fear_greed_index > 70 or fear_greed_index < 20: # Overbought sentiment or renewed extreme fear
                    self.last_signal_bar = ctx.bar_index # Reset cooldown
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "dynamic_sentiment_exit",
                            "fear_greed_index": fear_greed_index,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "rsi": rsi,
                            "price": current_price
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt strategy parameters based on position outcome
        pass