from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentPullback(Strategy):
    METADATA = {
        "name": "SolSentimentPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 14400,
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

        if len(closes) < self.lookback:
            return None

        current_price = ctx.bar.close
        sentiment = float(ctx.features.get("twitter_sentiment", 0.0))
        mean_24 = self._sma(closes, 24)

        if mean_24 is None:
            return None

        # Check existing position for exit
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean reversion or sentiment normalization exits
            if direction == "long":
                if current_price >= mean_24 or sentiment > 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_norm",
                            "price": current_price,
                            "sma24": mean_24,
                            "sentiment": sentiment,
                        },
                    )
            elif direction == "short":
                if current_price <= mean_24 or sentiment < 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_norm",
                            "price": current_price,
                            "sma24": mean_24,
                            "sentiment": sentiment,
                        },
                    )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate high/low reference for pullback
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])

        # Guard against zero division
        if recent_high <= 0 or recent_low <= 0:
            return None

        pullback_from_high = (recent_high - current_price) / recent_high
        rally_from_low = (current_price - recent_low) / recent_low

        # Gated setups:
        # Buy dips when sentiment is fearful (below -0.3) & pullback >= 3%
        if sentiment < -0.3 and pullback_from_high >= self.pullback_pct:
            confidence = min(0.9, 0.5 + abs(sentiment) * 0.3)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=550.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "fear_sentiment_dip_pullback",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "recent_high": recent_high,
                    "price": current_price,
                },
            )

        # Short rallies when sentiment is euphoric (above +0.3) & rally >= 3%
        if sentiment > 0.3 and rally_from_low >= self.pullback_pct:
            confidence = min(0.9, 0.5 + abs(sentiment) * 0.3)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=550.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "greed_sentiment_rally_exhaustion",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "recent_low": recent_low,
                    "price": current_price,
                },
            )

        return None