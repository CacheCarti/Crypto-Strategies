from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentAdaptivePullback(Strategy):
    METADATA = {
        "name": "SentimentAdaptivePullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.cooldown_period = 10
        self.last_exit_bar = -999
        self.bars_in_pos = 0

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_pos = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        current_price = ctx.bar.close
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        mean_price = self._sma(closes, self.lookback)
        if mean_price is None:
            return None

        # Position monitoring and mean reversion exit
        if ctx.has_position():
            self.bars_in_pos += 1
            direction = ctx.position_direction()

            # Exit when price reverts to mean or sentiment normalizes near neutral
            if direction == "long":
                if current_price >= mean_price or sentiment > 0.15 or self.bars_in_pos > 24:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_take_profit_or_neutral_sentiment",
                            "price": current_price,
                            "mean": mean_price,
                            "sentiment": sentiment,
                            "bars_held": self.bars_in_pos
                        }
                    )
            elif direction == "short":
                if current_price <= mean_price or sentiment < -0.15 or self.bars_in_pos > 24:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_take_profit_or_neutral_sentiment",
                            "price": current_price,
                            "mean": mean_price,
                            "sentiment": sentiment,
                            "bars_held": self.bars_in_pos
                        }
                    )
            return None

        # Entry logic: Adaptive depth pullback relative to rolling high/low
        rolling_high = max(highs[-self.lookback:])
        rolling_low = min(lows[-self.lookback:])

        if rolling_high <= 0 or rolling_low <= 0:
            return None

        pullback_from_high = (rolling_high - current_price) / rolling_high
        rally_from_low = (current_price - rolling_low) / rolling_low

        # Dynamic thresholds: Require deeper discount if sentiment is only mildly fearful
        # Long trigger: fearful sentiment (< -0.25) + meaningful pullback (> 3.5%)
        if sentiment < -0.25 and pullback_from_high >= 0.035:
            conf = min(0.9, 0.55 + abs(sentiment) * 0.35)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_fear_dip_buy",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high * 100,
                    "rolling_high": rolling_high,
                    "price": current_price
                }
            )

        # Short trigger: euphoric sentiment (> +0.25) + extended rally (> 3.5%)
        if sentiment > 0.25 and rally_from_low >= 0.035:
            conf = min(0.9, 0.55 + sentiment * 0.35)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_euphoria_fade",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low * 100,
                    "rolling_low": rolling_low,
                    "price": current_price
                }
            )

        return None