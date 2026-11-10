from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentEventPullback(Strategy):
    METADATA = {
        "name": "SolSentimentEventPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.cooldown_period = 12
        self.last_exit_bar = -999
        self.in_cooldown = False

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Check market crisis filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_regime_exit", "crisis_score": crisis_score}
                )
            return None

        current_close = closes[-1]
        mean_24 = self._sma(closes, 24)
        if mean_24 is None:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)

        # Position management and dynamic mean reversion / sentiment normalization exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                if current_close >= mean_24 or sentiment > -0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_normalized",
                            "price": current_close,
                            "sma24": mean_24,
                            "sentiment": sentiment,
                        }
                    )
            elif direction == "short":
                if current_close <= mean_24 or sentiment < 0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_normalized",
                            "price": current_close,
                            "sma24": mean_24,
                            "sentiment": sentiment,
                        }
                    )
            return None

        # Strict Cooldown after previous trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Event boundary evaluation: Pullback / Rally from recent extreme
        highest_high = max(highs[-self.lookback:])
        lowest_low = min(lows[-self.lookback:])

        pullback_from_high = (highest_high - current_close) / highest_high
        rally_from_low = (current_close - lowest_low) / lowest_low

        # Entry logic: 
        # Long event: Fear gate (< -0.30) & distinct >= 3.0% dip from 48-bar high & oversold extension below SMA24
        if sentiment < -0.30 and pullback_from_high >= 0.030 and current_close < mean_24:
            conf = min(0.9, 0.5 + abs(sentiment) * 0.3 + pullback_from_high * 2.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=320.0,
                take_profit_bps=520.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "sentiment_fear_pullback_dip",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high * 100,
                    "highest_high": highest_high,
                    "price": current_close,
                    "sma24": mean_24,
                }
            )

        # Short event: Euphoria gate (> 0.30) & distinct >= 3.0% rally from 48-bar low & overbought extension above SMA24
        if sentiment > 0.30 and rally_from_low >= 0.030 and current_close > mean_24:
            conf = min(0.9, 0.5 + sentiment * 0.3 + rally_from_low * 2.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=320.0,
                take_profit_bps=520.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "sentiment_euphoria_rally_stretch",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low * 100,
                    "lowest_low": lowest_low,
                    "price": current_close,
                    "sma24": mean_24,
                }
            )

        return None