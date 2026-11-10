from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGatedPullbackSol(Strategy):
    METADATA = {
        "name": "SentimentGatedPullbackSol",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
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

    def _rsi(self, closes, period: int = 14):
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
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback or len(highs) < self.lookback or len(lows) < self.lookback:
            return None

        # Cooldown guard after position close
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        current_close = ctx.bar.close
        sma20 = self._sma(closes, 20)
        rsi = self._rsi(closes, 14)

        if sma20 is None or rsi is None:
            return None

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean reversion or sentiment normalization exit
            if direction == "long":
                if current_close >= sma20 or sentiment >= 0.0 or rsi > 65.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_norm",
                            "close": current_close,
                            "sma20": sma20,
                            "sentiment": sentiment,
                            "rsi": rsi,
                        },
                    )
            elif direction == "short":
                if current_close <= sma20 or sentiment <= 0.0 or rsi < 35.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_norm",
                            "close": current_close,
                            "sma20": sma20,
                            "sentiment": sentiment,
                            "rsi": rsi,
                        },
                    )
            return None

        # Lookback rolling extreme prices (excluding current bar to avoid bias)
        recent_high = max(highs[-self.lookback:-1])
        recent_low = min(lows[-self.lookback:-1])

        pullback_from_high = (recent_high - current_close) / recent_high
        rally_from_low = (current_close - recent_low) / recent_low

        # Buy pullback during fear
        if sentiment < -0.3 and pullback_from_high >= self.pullback_pct and rsi < 42.0:
            confidence = min(0.85, 0.5 + abs(sentiment) * 0.35)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=300.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "sentiment_fear_pullback_entry",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "recent_high": recent_high,
                    "rsi": rsi,
                    "close": current_close,
                },
            )

        # Short rally during euphoria
        if sentiment > 0.3 and rally_from_low >= self.pullback_pct and rsi > 58.0:
            confidence = min(0.85, 0.5 + abs(sentiment) * 0.35)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=300.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "sentiment_euphoria_rally_entry",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "recent_low": recent_low,
                    "rsi": rsi,
                    "close": current_close,
                },
            )

        return None