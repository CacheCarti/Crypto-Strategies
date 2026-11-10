from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGatedPullbackSol(Strategy):
    METADATA = {
        "name": "SentimentGatedPullbackSol",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.pullback_pct = 0.03
        self.lookback = 48
        self.cooldown_bars = 8
        self.last_exit_bar = -999

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        mean_24 = self._sma(closes, 24)
        if mean_24 is None:
            return None

        current_price = ctx.bar.close

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            should_exit = False
            exit_reason = ""

            if pos_dir == "long":
                if current_price >= mean_24 or sentiment > -0.05:
                    should_exit = True
                    exit_reason = "mean_reversion_or_sentiment_normalized_long"
            elif pos_dir == "short":
                if current_price <= mean_24 or sentiment < 0.05:
                    should_exit = True
                    exit_reason = "mean_reversion_or_sentiment_normalized_short"

            if should_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "sentiment": sentiment,
                        "price": current_price,
                        "mean_24": mean_24,
                    },
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Evaluate pullback setups
        highest_high = max(highs[-self.lookback:])
        lowest_low = min(lows[-self.lookback:])

        pullback_from_high = (highest_high - current_price) / highest_high
        bounce_from_low = (current_price - lowest_low) / lowest_low

        # Long: fearful sentiment + 3% pullback from recent high, buying before moving above mean
        if sentiment < -0.25 and pullback_from_high >= self.pullback_pct and current_price < mean_24:
            conf = min(0.9, 0.5 + abs(sentiment) * 0.4)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_fear_pullback_dip",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "highest_high": highest_high,
                    "mean_24": mean_24,
                },
            )

        # Short: euphoric sentiment + 3% rally from recent low, selling before moving below mean
        if sentiment > 0.25 and bounce_from_low >= self.pullback_pct and current_price > mean_24:
            conf = min(0.9, 0.5 + abs(sentiment) * 0.4)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_greed_rally_fade",
                    "sentiment": sentiment,
                    "bounce_pct": bounce_from_low,
                    "lowest_low": lowest_low,
                    "mean_24": mean_24,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index