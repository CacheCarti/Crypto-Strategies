from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentGatedPullback(Strategy):
    METADATA = {
        "name": "SolSentimentGatedPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 1)
        highs = ctx.highs(self.lookback + 1)
        lows = ctx.lows(self.lookback + 1)

        if len(closes) < self.lookback + 1:
            return None

        current_close = ctx.bar.close
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        mean_24 = self._sma(closes, 24)
        if mean_24 is None:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Handle active position exit conditions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            should_flat = False
            exit_reason = ""

            if pos_dir == "long":
                if current_close >= mean_24:
                    should_flat = True
                    exit_reason = "mean_reversion_achieved_long"
                elif sentiment > 0.0:
                    should_flat = True
                    exit_reason = "sentiment_normalized_long"
            elif pos_dir == "short":
                if current_close <= mean_24:
                    should_flat = True
                    exit_reason = "mean_reversion_achieved_short"
                elif sentiment < 0.0:
                    should_flat = True
                    exit_reason = "sentiment_normalized_short"

            if should_flat:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "sentiment": sentiment,
                        "price": current_close,
                        "mean_24": mean_24,
                    },
                )
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entering during extreme meltdown / crisis states
        if regime == "MELTDOWN" or crisis_score > 0.65:
            return None

        highest_n = max(highs[-self.lookback:])
        lowest_n = min(lows[-self.lookback:])

        pullback_from_high = (highest_n - current_close) / highest_n
        rally_from_low = (current_close - lowest_n) / lowest_n

        # Long Setup: Fearful sentiment + sufficient dip from recent swing high
        if sentiment < -0.30 and pullback_from_high >= self.pullback_pct:
            confidence = min(0.9, 0.6 + abs(sentiment) * 0.3)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=600.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "sentiment_fear_pullback_long",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "highest_n": highest_n,
                    "price": current_close,
                },
            )

        # Short Setup: Euphoric sentiment + extended rally from recent swing low
        if sentiment > 0.30 and rally_from_low >= self.pullback_pct:
            confidence = min(0.9, 0.6 + abs(sentiment) * 0.3)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=600.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "sentiment_euphoria_rally_short",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "lowest_n": lowest_n,
                    "price": current_close,
                },
            )

        return None