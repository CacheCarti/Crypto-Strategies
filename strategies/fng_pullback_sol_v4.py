from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SentimentPullbackReversion(Strategy):
    METADATA = {
        "name": "Sentiment Pullback Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_window = 42
        self.mean_period = 21
        self.rsi_period = 14
        self.pullback_threshold = 0.028  # 2.8% pullback/extension
        self.sentiment_fear_threshold = -0.22
        self.sentiment_greed_threshold = 0.22
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.last_entry_bar = -100

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
        gains = []
        losses = []
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_window + 5)
        highs = ctx.highs(self.lookback_window + 5)
        lows = ctx.lows(self.lookback_window + 5)

        if len(closes) < self.lookback_window + 2:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        current_close = ctx.bar.close
        ema_mean = self._ema(closes, self.mean_period)
        rsi_val = self._rsi(closes, self.rsi_period)

        if ema_mean is None or rsi_val is None:
            return None

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit Management
        if has_pos:
            if pos_dir == "long":
                # Exit when price reverts to mean, sentiment normalizes, or RSI overbought
                if current_close >= ema_mean or sentiment >= 0.08 or rsi_val >= 66.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_normalized",
                            "price": current_close,
                            "ema_mean": round(ema_mean, 2),
                            "sentiment": round(sentiment, 3),
                            "rsi": round(rsi_val, 2),
                        },
                    )
            elif pos_dir == "short":
                # Exit when price reverts to mean, sentiment normalizes, or RSI oversold
                if current_close <= ema_mean or sentiment <= -0.08 or rsi_val <= 34.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_normalized",
                            "price": current_close,
                            "ema_mean": round(ema_mean, 2),
                            "sentiment": round(sentiment, 3),
                            "rsi": round(rsi_val, 2),
                        },
                    )
            return None

        # Entry Guard: Cooldown checks to prevent overtrading
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        window_high = max(highs[-self.lookback_window :])
        window_low = min(lows[-self.lookback_window :])

        # Long Setup: Fear sentiment + pullback from rolling high + RSI dip confirmation
        pullback_pct = (window_high - current_close) / window_high
        if sentiment <= self.sentiment_fear_threshold and pullback_pct >= self.pullback_threshold:
            if rsi_val <= 44.0 and current_close < ema_mean:
                self.last_entry_bar = ctx.bar_index
                conf = min(0.85, 0.60 + abs(sentiment) * 0.35)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "fear_sentiment_dip_pullback",
                        "sentiment": round(sentiment, 3),
                        "pullback_pct": round(pullback_pct * 100, 2),
                        "window_high": round(window_high, 2),
                        "rsi": round(rsi_val, 2),
                        "ema_mean": round(ema_mean, 2),
                    },
                )

        # Short Setup: Greed/Euphoria sentiment + rally extension from rolling low + RSI top
        rally_pct = (current_close - window_low) / window_low
        if sentiment >= self.sentiment_greed_threshold and rally_pct >= self.pullback_threshold:
            if rsi_val >= 56.0 and current_close > ema_mean:
                self.last_entry_bar = ctx.bar_index
                conf = min(0.85, 0.60 + sentiment * 0.35)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "greed_sentiment_rally_exhaustion",
                        "sentiment": round(sentiment, 3),
                        "rally_pct": round(rally_pct * 100, 2),
                        "window_low": round(window_low, 2),
                        "rsi": round(rsi_val, 2),
                        "ema_mean": round(ema_mean, 2),
                    },
                )

        return None