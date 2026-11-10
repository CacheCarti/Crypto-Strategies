from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentPullback(Strategy):
    METADATA = {
        "name": "SolSentimentPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.bars_in_position = 0

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        curr_close = closes[-1]
        prev_close = closes[-2]
        sma20 = self._sma(closes, 20)

        if sma20 is None:
            return None

        # Manage open position exits
        if ctx.has_position():
            self.bars_in_position += 1
            direction = ctx.position_direction()

            # Exit if sentiment normalizes or price reverts to 20 SMA
            if direction == "long":
                if curr_close >= sma20 or sentiment >= 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_norm",
                            "close": curr_close,
                            "sma20": sma20,
                            "sentiment": sentiment,
                            "bars_held": self.bars_in_position,
                        },
                    )
            elif direction == "short":
                if curr_close <= sma20 or sentiment <= 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_norm",
                            "close": curr_close,
                            "sma20": sma20,
                            "sentiment": sentiment,
                            "bars_held": self.bars_in_position,
                        },
                    )
            return None

        # Check cooldown to prevent excessive trading and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        highest_high = max(highs[-self.lookback - 1 : -1])
        lowest_low = min(lows[-self.lookback - 1 : -1])

        pullback_threshold = highest_high * (1.0 - self.pullback_pct)
        rally_threshold = lowest_low * (1.0 + self.pullback_pct)

        # Event boundary: discrete cross below 3% pullback level while fearful
        if sentiment < -0.30:
            if prev_close >= pullback_threshold and curr_close < pullback_threshold:
                conf = min(1.0, 0.65 + abs(sentiment) * 0.3)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fear_pullback_discrete_cross",
                        "sentiment": sentiment,
                        "highest_high": highest_high,
                        "close": curr_close,
                        "threshold": pullback_threshold,
                    },
                )

        # Event boundary: discrete cross above 3% rally level while euphoric
        if sentiment > 0.30:
            if prev_close <= rally_threshold and curr_close > rally_threshold:
                conf = min(1.0, 0.65 + sentiment * 0.3)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "euphoria_rally_discrete_cross",
                        "sentiment": sentiment,
                        "lowest_low": lowest_low,
                        "close": curr_close,
                        "threshold": rally_threshold,
                    },
                )

        return None