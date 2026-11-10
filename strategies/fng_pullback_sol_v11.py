from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGatedPullback(Strategy):
    METADATA = {
        "name": "SentimentGatedPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.sentiment_threshold = 0.25
        self.cooldown_period = 8
        self.last_exit_bar = -100

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)
        if len(closes) < self.lookback or len(highs) < self.lookback or len(lows) < self.lookback:
            return None

        current_close = closes[-1]
        mean_24 = self._sma(closes, 24)
        if mean_24 is None:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Handle active position exits: mean reversion or sentiment normalization
        if has_pos:
            if pos_dir == "long":
                if current_close >= mean_24 or sentiment >= 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_reverted_or_sentiment_normalized",
                            "sentiment": sentiment,
                            "current_close": current_close,
                            "mean_24": mean_24
                        }
                    )
            elif pos_dir == "short":
                if current_close <= mean_24 or sentiment <= 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_reverted_or_sentiment_normalized",
                            "sentiment": sentiment,
                            "current_close": current_close,
                            "mean_24": mean_24
                        }
                    )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        highest_h = max(highs[-self.lookback:])
        lowest_l = min(lows[-self.lookback:])

        pullback_from_high = (highest_h - current_close) / highest_h if highest_h > 0 else 0.0
        rally_from_low = (current_close - lowest_l) / lowest_l if lowest_l > 0 else 0.0

        # Long Entry: Fearful sentiment & sharp pullback from local high
        if sentiment < -self.sentiment_threshold and pullback_from_high >= self.pullback_pct:
            confidence = min(0.9, 0.5 + abs(sentiment) * 0.4)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "fear_sentiment_dip_pullback",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "highest_48": highest_h,
                    "close": current_close
                }
            )

        # Short Entry: Euphoric sentiment & extended rally from local low
        if sentiment > self.sentiment_threshold and rally_from_low >= self.pullback_pct:
            confidence = min(0.9, 0.5 + abs(sentiment) * 0.4)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "euphoria_sentiment_rally_fade",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "lowest_48": lowest_l,
                    "close": current_close
                }
            )

        return None