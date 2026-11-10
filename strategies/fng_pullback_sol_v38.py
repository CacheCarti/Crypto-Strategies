from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentEventPullback(Strategy):
    METADATA = {
        "name": "SolSentimentEventPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.035
        self.last_exit_bar = -999
        self.cooldown_bars = 12
        self.min_hold_bars = 4

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        if len(closes) < self.lookback + 1:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        current_close = ctx.bar.close

        # Compute rolling window bounds and mean
        window = closes[-self.lookback:]
        high_ref = max(window)
        low_ref = min(window)
        mean_ref = sum(window) / self.lookback

        # Position management / Mean reversion & normalization exits
        if ctx.has_position():
            pos = ctx.position
            direction = pos.get("direction") if pos else ctx.position_direction()
            bars_held = ctx.bar_index - self.last_exit_bar if self.last_exit_bar > 0 else 10

            if direction == "long":
                if current_close >= mean_ref or sentiment >= -0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_norm",
                            "close": current_close,
                            "mean": mean_ref,
                            "sentiment": sentiment,
                        }
                    )
            elif direction == "short":
                if current_close <= mean_ref or sentiment <= 0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_norm",
                            "close": current_close,
                            "mean": mean_ref,
                            "sentiment": sentiment,
                        }
                    )
            return None

        # Hard cooldown enforcement between setups
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Gated Event Boundaries: 
        # Long: Sentiment is deeply fearful (<-0.25) + substantial pullback from recent high (>=3.5%)
        # Short: Sentiment is euphoric (>0.25) + substantial rally from recent low (>=3.5%)
        pullback_from_high = (high_ref - current_close) / high_ref
        rally_from_low = (current_close - low_ref) / low_ref

        if sentiment <= -0.25 and pullback_from_high >= self.pullback_pct:
            confidence = min(0.9, 0.5 + abs(sentiment) * 0.4)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_sentiment_dip_pullback",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "high_ref": high_ref,
                    "close": current_close,
                    "mean_ref": mean_ref,
                }
            )

        if sentiment >= 0.25 and rally_from_low >= self.pullback_pct:
            confidence = min(0.9, 0.5 + abs(sentiment) * 0.4)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "euphoria_sentiment_rally_exhaustion",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "low_ref": low_ref,
                    "close": current_close,
                    "mean_ref": mean_ref,
                }
            )

        return None