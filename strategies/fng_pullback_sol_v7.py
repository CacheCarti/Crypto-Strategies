from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentPullback(Strategy):
    METADATA = {
        "name": "SolSentimentPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 360.0,
        "declared_tp_bps": 680.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 42
        self.pullback_threshold = 0.028
        self.sentiment_threshold = 0.25
        self.rsi_period = 14
        self.ema_period = 21
        self.cooldown_bars = 6
        self.last_exit_bar = -100

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        sentiment = float(ctx.features.get("twitter_sentiment", 0.0))
        rsi = self._rsi(closes, self.rsi_period)
        ema21 = self._ema(closes, self.ema_period)

        if rsi is None or ema21 is None:
            return None

        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])

        pullback_from_high = (recent_high - current_close) / recent_high if recent_high > 0 else 0.0
        rally_from_low = (current_close - recent_low) / recent_low if recent_low > 0 else 0.0

        # Position Management & Mean Reversion Exit Logic
        if ctx.has_position():
            direction = ctx.position_direction()
            
            # Long Exit: Price crossed above EMA21, RSI normalized > 52, or sentiment flipped positive
            if direction == "long":
                if current_close >= ema21 or rsi >= 55.0 or sentiment >= 0.15:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_target_hit",
                            "close": current_close,
                            "ema21": ema21,
                            "rsi": rsi,
                            "sentiment": sentiment,
                        },
                    )

            # Short Exit: Price crossed below EMA21, RSI normalized < 48, or sentiment flipped negative
            elif direction == "short":
                if current_close <= ema21 or rsi <= 45.0 or sentiment <= -0.15:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_target_hit",
                            "close": current_close,
                            "ema21": ema21,
                            "rsi": rsi,
                            "sentiment": sentiment,
                        },
                    )
            return None

        # Mandatory Cooldown Gate
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Entry Logic
        # Long Entry: Fearful sentiment + Dip from high + RSI confirming pullback
        if sentiment <= -self.sentiment_threshold and pullback_from_high >= self.pullback_threshold and rsi <= 42.0:
            confidence = min(0.9, 0.55 + (abs(sentiment) * 0.25) + (pullback_from_high * 2.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_sentiment_dip_pullback",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high * 100.0,
                    "rsi": rsi,
                    "recent_high": recent_high,
                    "ema21": ema21,
                    "close": current_close,
                },
            )

        # Short Entry: Euphoric sentiment + Rally from low + RSI confirming stretched rally
        if sentiment >= self.sentiment_threshold and rally_from_low >= self.pullback_threshold and rsi >= 58.0:
            confidence = min(0.9, 0.55 + (sentiment * 0.25) + (rally_from_low * 2.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "euphoria_sentiment_rally_fade",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low * 100.0,
                    "rsi": rsi,
                    "recent_low": recent_low,
                    "ema21": ema21,
                    "close": current_close,
                },
            )

        return None