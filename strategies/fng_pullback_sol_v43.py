from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGatedPullbackSol(Strategy):
    METADATA = {
        "name": "SentimentGatedPullbackSol",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # ~8 hours typical hold
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.035  # 3.5% pullback / rally threshold
        self.cooldown_bars = 6     # Wait 6 hours after exit before new entry
        self.last_exit_bar = -100

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        highs = ctx.highs(self.lookback + 1)
        lows = ctx.lows(self.lookback + 1)

        current_close = closes[-1]
        prev_close = closes[-2]
        
        # Slow filter: twitter_sentiment (derived from F&G, range -1.0 to 1.0)
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        sma20 = self._sma(closes, 20)

        # 1. POSITION MANAGEMENT & EXITS
        if ctx.has_position():
            direction = ctx.position_direction()
            
            # Mean reversion or sentiment normalization exit
            if direction == "long":
                if (sma20 is not None and current_close >= sma20) or sentiment >= 0.0:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "long_take_profit_or_sentiment_normalized",
                        "close": current_close,
                        "sma20": sma20 if sma20 else 0.0,
                        "sentiment": sentiment
                    })
            elif direction == "short":
                if (sma20 is not None and current_close <= sma20) or sentiment <= 0.0:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "short_take_profit_or_sentiment_normalized",
                        "close": current_close,
                        "sma20": sma20 if sma20 else 0.0,
                        "sentiment": sentiment
                    })
            return None

        # 2. ENTRY LOGIC
        # Respect cooldown to prevent overtrading and fee drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        recent_high = max(highs[-self.lookback:-1])
        recent_low = min(lows[-self.lookback:-1])

        # Long Entry: Fearful regime + 3.5% dip from recent high + immediate tick rebound
        pullback_ratio = (recent_high - current_close) / recent_high if recent_high > 0 else 0.0
        if sentiment <= -0.30 and pullback_ratio >= self.pullback_pct:
            # Confirm local exhaustion: current bar closed higher than previous bar
            if current_close > prev_close:
                conf = min(0.9, 0.5 + abs(sentiment) * 0.4)
                return ctx.signal("long", confidence=conf, stop_loss_bps=400.0, take_profit_bps=500.0, metadata={
                    "reason": "sentiment_fear_dip_bought",
                    "sentiment": sentiment,
                    "pullback_ratio": pullback_ratio,
                    "high_48": recent_high,
                    "close": current_close
                })

        # Short Entry: Euphoric regime + 3.5% surge from recent low + immediate tick rejection
        rally_ratio = (current_close - recent_low) / recent_low if recent_low > 0 else 0.0
        if sentiment >= 0.30 and rally_ratio >= self.pullback_pct:
            # Confirm local exhaustion: current bar closed lower than previous bar
            if current_close < prev_close:
                conf = min(0.9, 0.5 + sentiment * 0.4)
                return ctx.signal("short", confidence=conf, stop_loss_bps=400.0, take_profit_bps=500.0, metadata={
                    "reason": "sentiment_euphoria_rally_faded",
                    "sentiment": sentiment,
                    "rally_ratio": rally_ratio,
                    "low_48": recent_low,
                    "close": current_close
                })

        return None