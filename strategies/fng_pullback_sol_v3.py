from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SentimentPullbackReversion(Strategy):
    METADATA = {
        "name": "Sentiment Pullback Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 580.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 42
        self.mean_period = 24
        self.rsi_period = 14
        self.pullback_pct_thresh = 0.028  # 2.8% pullback/rally trigger
        self.sentiment_fear_thresh = -0.22
        self.sentiment_greed_thresh = 0.22
        self.cooldown_bars = 7
        self.cooldown_until_bar = 0

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 5)
        highs = ctx.highs(self.lookback_period)
        lows = ctx.lows(self.lookback_period)

        if len(closes) < self.lookback_period or len(highs) < self.lookback_period:
            return None

        current_close = ctx.bar.close
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Do not open entries during severe crisis regimes
        if crisis_score > 0.75:
            return None

        mean_ema = self._ema(closes, self.mean_period)
        rsi = self._rsi(closes, self.rsi_period)

        if mean_ema is None or rsi is None:
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                # Exit when price reverts back to mean or sentiment flips positive
                if current_close >= mean_ema or sentiment >= 0.10:
                    self.cooldown_until_bar = ctx.bar_index + self.cooldown_bars
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reverted_or_sentiment_normalized",
                            "close": current_close,
                            "ema_mean": mean_ema,
                            "sentiment": sentiment,
                            "rsi": rsi,
                        },
                    )
            elif pos_dir == "short":
                # Exit when price reverts back down to mean or sentiment cools off
                if current_close <= mean_ema or sentiment <= -0.10:
                    self.cooldown_until_bar = ctx.bar_index + self.cooldown_bars
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reverted_or_sentiment_normalized",
                            "close": current_close,
                            "ema_mean": mean_ema,
                            "sentiment": sentiment,
                            "rsi": rsi,
                        },
                    )
            return None

        # Check entry cooldown
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        rolling_high = max(highs)
        rolling_low = min(lows)

        # Pullback from rolling high
        pullback_from_high = (rolling_high - current_close) / rolling_high if rolling_high > 0 else 0.0
        # Rally from rolling low
        rally_from_low = (current_close - rolling_low) / rolling_low if rolling_low > 0 else 0.0

        # Long Setup: Sentiment fear + price pull back from recent peak + RSI not fully crashed
        if (
            sentiment <= self.sentiment_fear_thresh
            and pullback_from_high >= self.pullback_pct_thresh
            and rsi < 44.0
        ):
            confidence = min(0.85, 0.55 + abs(sentiment) * 0.3)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_pullback_dip_entry",
                    "sentiment": sentiment,
                    "pullback_pct": round(pullback_from_high * 100, 2),
                    "rsi": round(rsi, 2),
                    "rolling_high": rolling_high,
                    "ema_mean": round(mean_ema, 2),
                },
            )

        # Short Setup: Sentiment euphoria + price rally extension + elevated RSI
        if (
            sentiment >= self.sentiment_greed_thresh
            and rally_from_low >= self.pullback_pct_thresh
            and rsi > 56.0
        ):
            confidence = min(0.85, 0.55 + abs(sentiment) * 0.3)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "euphoria_rally_fade_entry",
                    "sentiment": sentiment,
                    "rally_pct": round(rally_from_low * 100, 2),
                    "rsi": round(rsi, 2),
                    "rolling_low": rolling_low,
                    "ema_mean": round(mean_ema, 2),
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = max(self.cooldown_until_bar, ctx.bar_index + self.cooldown_bars)