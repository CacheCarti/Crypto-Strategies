from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentPullbackEventStrategy(Strategy):
    METADATA = {
        "name": "SentimentPullbackEventStrategy",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.035
        self.sentiment_extreme = 0.25
        self.last_exit_bar = -999
        self.cooldown_bars = 16
        self.entry_bar = -999

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

        if len(closes) < self.lookback + 2:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        mean_24 = self._sma(closes, 24)
        if mean_24 is None:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]
        current_bar = ctx.bar_index

        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = current_bar - self.entry_bar

            if direction == "long":
                if current_close >= mean_24 or sentiment >= 0.0 or bars_held >= 30:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_exit_mean_or_sentiment_normalization",
                            "close": current_close,
                            "sma24": mean_24,
                            "sentiment": sentiment,
                            "bars_held": bars_held,
                        },
                    )
            elif direction == "short":
                if current_close <= mean_24 or sentiment <= -0.0 or bars_held >= 30:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_exit_mean_or_sentiment_normalization",
                            "close": current_close,
                            "sma24": mean_24,
                            "sentiment": sentiment,
                            "bars_held": bars_held,
                        },
                    )
            return None

        if current_bar - self.last_exit_bar < self.cooldown_bars:
            return None

        highest_ref = max(highs[-self.lookback - 1 : -1])
        lowest_ref = min(lows[-self.lookback - 1 : -1])

        long_dip_threshold = highest_ref * (1.0 - self.pullback_pct)
        short_rally_threshold = lowest_ref * (1.0 + self.pullback_pct)

        long_event_trigger = (prev_close > long_dip_threshold) and (current_close <= long_dip_threshold)
        short_event_trigger = (prev_close < short_rally_threshold) and (current_close >= short_rally_threshold)

        if sentiment <= -self.sentiment_extreme and long_event_trigger:
            self.entry_bar = current_bar
            conf = min(0.9, 0.6 + abs(sentiment) * 0.3)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_sentiment_dip_event_cross",
                    "sentiment": sentiment,
                    "close": current_close,
                    "highest_ref": highest_ref,
                    "dip_threshold": long_dip_threshold,
                },
            )

        if sentiment >= self.sentiment_extreme and short_event_trigger:
            self.entry_bar = current_bar
            conf = min(0.9, 0.6 + abs(sentiment) * 0.3)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "euphoria_sentiment_rally_event_cross",
                    "sentiment": sentiment,
                    "close": current_close,
                    "lowest_ref": lowest_ref,
                    "rally_threshold": short_rally_threshold,
                },
            )

        return None