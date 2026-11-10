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
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.sentiment_long_thresh = -0.25
        self.sentiment_short_thresh = 0.25
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback:
            return None

        current_close = ctx.bar.close
        sma_val = self._sma(closes, 24)
        if sma_val is None:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)

        # Active position management: exit on mean reversion or sentiment normalization
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if current_close >= sma_val or sentiment > 0.10:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "long_tp_or_sentiment_normalized",
                        "price": current_close,
                        "sma": sma_val,
                        "sentiment": sentiment
                    })
            elif pos_dir == "short":
                if current_close <= sma_val or sentiment < -0.10:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "short_tp_or_sentiment_normalized",
                        "price": current_close,
                        "sma": sma_val,
                        "sentiment": sentiment
                    })
            return None

        # Mandatory cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Determine reference highs and lows over the lookback window
        window_high = max(highs[-self.lookback:])
        window_low = min(lows[-self.lookback:])

        pullback_from_high = (window_high - current_close) / window_high
        rally_from_low = (current_close - window_low) / window_low

        # Long: sentiment is fearful & price pulled back at least 3% from local high
        if sentiment <= self.sentiment_long_thresh and pullback_from_high >= self.pullback_pct:
            confidence = min(0.85, 0.50 + abs(sentiment) * 0.35)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fearful_dip_pullback",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "window_high": window_high,
                    "price": current_close,
                    "sma": sma_val
                }
            )

        # Short: sentiment is euphoric & price rallied at least 3% from local low
        if sentiment >= self.sentiment_short_thresh and rally_from_low >= self.pullback_pct:
            confidence = min(0.85, 0.50 + abs(sentiment) * 0.35)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "euphoric_rally_exhaustion",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "window_low": window_low,
                    "price": current_close,
                    "sma": sma_val
                }
            )

        return None