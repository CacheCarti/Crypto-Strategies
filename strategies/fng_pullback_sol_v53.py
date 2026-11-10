from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentPullback(Strategy):
    METADATA = {
        "name": "SolSentimentPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold on 1h bars
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 6
        self.last_exit_bar = -999

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

        # Cooldown guard to avoid trade-clustering and excessive friction
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Slow Sentiment Gate (-1.0 to 1.0)
        sentiment = ctx.features.get("twitter_sentiment", 0.0)

        # Reference price levels
        current_close = ctx.bar.close
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        mean_24 = self._sma(closes, 24)

        if mean_24 is None:
            return None

        # If already in position, manage dynamic mean-reversion / sentiment-normalization exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Long exit: sentiment normalized or price reverted above short-term mean
            if pos_dir == "long":
                if sentiment > -0.05 or current_close >= mean_24:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_reversion_or_sentiment_normalized",
                            "sentiment": sentiment,
                            "price": current_close,
                            "sma24": mean_24,
                        }
                    )
            # Short exit: sentiment normalized or price reverted below short-term mean
            elif pos_dir == "short":
                if sentiment < 0.05 or current_close <= mean_24:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_reversion_or_sentiment_normalized",
                            "sentiment": sentiment,
                            "price": current_close,
                            "sma24": mean_24,
                        }
                    )
            return None

        # Avoid crisis dislocations
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Pullback calculations from local rolling extremes
        drop_from_high = (recent_high - current_close) / recent_high
        rally_from_low = (current_close - recent_low) / recent_low

        # 1. Long Entry: Fearful sentiment + distinct >= 3% pullback from 48h peak
        if sentiment < -0.30 and drop_from_high >= self.pullback_pct:
            confidence = min(0.9, max(0.5, 0.5 + abs(sentiment) * 0.3))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=550.0,
                metadata={
                    "reason": "sentiment_fear_dip_pullback",
                    "sentiment": sentiment,
                    "drop_pct": drop_from_high,
                    "recent_high": recent_high,
                    "price": current_close,
                }
            )

        # 2. Short Entry: Euphoric sentiment + distinct >= 3% rally from 48h trough
        if sentiment > 0.30 and rally_from_low >= self.pullback_pct:
            confidence = min(0.9, max(0.5, 0.5 + sentiment * 0.3))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=550.0,
                metadata={
                    "reason": "sentiment_euphoria_rally_stretch",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "recent_low": recent_low,
                    "price": current_close,
                }
            )

        return None