from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentEventPullback(Strategy):
    METADATA = {
        "name": "SolSentimentEventPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.035
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.max_hold_bars = 24

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = closes[-1]
        sentiment = float(ctx.features.get("twitter_sentiment", 0.0))
        mean_24 = self._sma(closes, 24)
        if mean_24 is None:
            return None

        # Position management: explicit event boundaries and mean reversion exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Long exit rules
            if pos_dir == "long":
                if current_price >= mean_24 or sentiment > -0.05 or bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_mean_revert_or_sentiment_norm",
                        "price": current_price,
                        "sma24": mean_24,
                        "sentiment": sentiment,
                        "bars_held": bars_held
                    })

            # Short exit rules
            elif pos_dir == "short":
                if current_price <= mean_24 or sentiment < 0.05 or bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_mean_revert_or_sentiment_norm",
                        "price": current_price,
                        "sma24": mean_24,
                        "sentiment": sentiment,
                        "bars_held": bars_held
                    })

            return None

        # Hard Cooldown Guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])

        # Event: Fear-driven Dip Buying
        # Sentiment deeply fearful (<= -0.3) & price pulled back at least 3.5% from 48-bar high
        if sentiment <= -0.30:
            pullback_from_high = (recent_high - current_price) / recent_high
            if pullback_from_high >= self.pullback_pct and current_price < mean_24:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.5 + abs(sentiment) * 0.4)
                return ctx.signal("long", confidence=conf, metadata={
                    "reason": "fear_pullback_dip_entry",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "recent_high": recent_high,
                    "price": current_price,
                    "sma24": mean_24
                })

        # Event: Greed-driven Rally Fading
        # Sentiment euphoric (>= 0.3) & price rallied at least 3.5% from 48-bar low
        if sentiment >= 0.30:
            rally_from_low = (current_price - recent_low) / recent_low
            if rally_from_low >= self.pullback_pct and current_price > mean_24:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.5 + sentiment * 0.4)
                return ctx.signal("short", confidence=conf, metadata={
                    "reason": "euphoria_rally_fade_entry",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "recent_low": recent_low,
                    "price": current_price,
                    "sma24": mean_24
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index