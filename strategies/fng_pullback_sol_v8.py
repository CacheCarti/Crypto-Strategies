from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGatedPullback(Strategy):
    METADATA = {
        "name": "Sentiment Gated Sol Pullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 620.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 42
        self.pullback_pct = 0.028  # 2.8% pullback threshold
        self.ema_period = 21
        self.sentiment_fear_thresh = -0.22
        self.sentiment_greed_thresh = 0.22
        self.cooldown_bars = 5
        self.last_exit_bar = -999

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback:
            return None

        current_close = ctx.bar.close
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        ema_mean = self._ema(closes, self.ema_period)
        if ema_mean is None:
            return None

        bars_since_exit = ctx.bar_index - self.last_exit_bar
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic for open positions
        if has_pos:
            # Long exit: price recovered above EMA mean or sentiment flipped back to greed/neutral
            if pos_dir == "long":
                if current_close >= ema_mean or sentiment > 0.15:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_target_hit",
                            "sentiment": round(sentiment, 3),
                            "ema_mean": round(ema_mean, 2),
                            "close": round(current_close, 2)
                        }
                    )
            # Short exit: price pulled back below EMA mean or sentiment normalized to fear
            elif pos_dir == "short":
                if current_close <= ema_mean or sentiment < -0.15:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_target_hit",
                            "sentiment": round(sentiment, 3),
                            "ema_mean": round(ema_mean, 2),
                            "close": round(current_close, 2)
                        }
                    )
            return None

        # Entry logic: enforce cooldown after previous exits
        if bars_since_exit < self.cooldown_bars:
            return None

        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])

        # Calculate pullbacks
        drawdown_from_high = (recent_high - current_close) / recent_high if recent_high > 0 else 0.0
        rally_from_low = (current_close - recent_low) / recent_low if recent_low > 0 else 0.0

        # Long Setup: Sentiment in Fear (< -0.22) + Pullback >= 2.8% from rolling 42-bar high + below EMA
        if sentiment <= self.sentiment_fear_thresh and drawdown_from_high >= self.pullback_pct and current_close < ema_mean:
            confidence = min(1.0, 0.5 + abs(sentiment) * 0.4 + (drawdown_from_high - self.pullback_pct) * 5.0)
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_fear_dip_pullback",
                    "sentiment": round(sentiment, 3),
                    "drawdown_from_high_pct": round(drawdown_from_high * 100, 2),
                    "recent_high": round(recent_high, 2),
                    "ema_mean": round(ema_mean, 2)
                }
            )

        # Short Setup: Sentiment in Euphoria (> 0.22) + Rally >= 2.8% from rolling 42-bar low + above EMA
        if sentiment >= self.sentiment_greed_thresh and rally_from_low >= self.pullback_pct and current_close > ema_mean:
            confidence = min(1.0, 0.5 + sentiment * 0.4 + (rally_from_low - self.pullback_pct) * 5.0)
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_greed_rally_fade",
                    "sentiment": round(sentiment, 3),
                    "rally_from_low_pct": round(rally_from_low * 100, 2),
                    "recent_low": round(recent_low, 2),
                    "ema_mean": round(ema_mean, 2)
                }
            )

        return None