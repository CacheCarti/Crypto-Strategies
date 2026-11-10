from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFadeSOL(Strategy):
    METADATA = {
        "name": "SentimentFadeSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.momentum_period = 10
        self.rsi_period = 14
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        avg_gain = self._sma(gains, period)
        avg_loss = self._sma(losses, period)

        if avg_gain is None or avg_loss is None:
            return None

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _momentum(self, closes, period):
        if len(closes) < period:
            return None
        return closes[-1] - closes[-period]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.momentum_period + 1)
        highs = ctx.highs(self.rsi_period + 1)
        lows = ctx.lows(self.rsi_period + 1)

        if len(closes) < self.momentum_period + 1 or len(highs) < self.rsi_period + 1:
            return None

        current_price = ctx.bar.close

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Indicators
        momentum = self._momentum(closes, self.momentum_period)
        rsi = self._rsi(closes, self.rsi_period)

        if momentum is None or rsi is None:
            return None

        # Regime Filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")

        # ENTRY LOGIC: Fade rallies when in extreme greed and momentum is weakening
        # We are looking for short opportunities
        if not ctx.has_position():
            # Condition 1: Extreme Greed (Sentiment)
            is_extreme_greed = fear_greed_index > 70 # High greed
            
            # Condition 2: Momentum Loss (Price Action)
            # Price made a new high, but momentum is not as strong as before, or is negative
            # Check if current price is near recent highs but momentum is weak or negative
            recent_high = max(highs[-self.momentum_period:])
            
            # Additional check: RSI is overbought, indicating a rally that might reverse
            is_overbought = rsi > 70 

            # Combining conditions for a short signal
            if is_extreme_greed and is_overbought and current_price >= recent_high * 0.995 and momentum < 0:
                # Filter by market regime: Only fade rallies in normal or high volatility, not crisis
                if trend_regime != "bull" and regime in ["NORMAL", "HIGH_VOL"]:
                    confidence = 0.6 + (fear_greed_index - 70) / 30 * 0.4 # Higher greed, higher confidence
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short", 
                        confidence=confidence,
                        metadata={
                            "reason": "sentiment_fade_extreme_greed_momentum_loss",
                            "fear_greed_index": fear_greed_index,
                            "rsi": rsi,
                            "momentum": momentum,
                            "current_price": current_price,
                            "recent_high": recent_high,
                            "trend_regime": trend_regime,
                            "market_regime": regime,
                        }
                    )
        
        # EXIT LOGIC: If we have a position, consider exiting if conditions reverse or profit target is hit
        # The engine handles SL/TP, so we only need to manage discretionary exits
        if ctx.has_position() and ctx.position_direction() == "short":
            # If sentiment shifts from extreme greed, or RSI becomes oversold, consider exiting
            if fear_greed_index < 60 or rsi < 30:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "sentiment_fade_exit_sentiment_shift_or_rsi_oversold",
                        "fear_greed_index": fear_greed_index,
                        "rsi": rsi,
                        "current_price": current_price,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Add adaptive logic here, e.g., adjust confidence based on recent performance
        pass