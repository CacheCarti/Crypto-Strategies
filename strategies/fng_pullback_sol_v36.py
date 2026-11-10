from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentPullback(Strategy):
    METADATA = {
        "name": "SolSentimentPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.pullback_lookback = 48
        self.pullback_pct = 0.03
        self.rsi_period = 14
        self.last_exit_bar = -999
        self.cooldown_bars = 6

    def _rsi(self, closes, period=14):
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
        closes = ctx.closes(self.pullback_lookback + 2)
        highs = ctx.highs(self.pullback_lookback + 2)
        lows = ctx.lows(self.pullback_lookback + 2)

        if len(closes) < self.pullback_lookback + 1:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        mean_price = sum(closes[-24:]) / 24.0

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if current_close >= mean_price or sentiment >= 0.15:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_normalized",
                            "sentiment": sentiment,
                            "close": current_close,
                            "mean_price": mean_price,
                            "rsi": rsi,
                        },
                    )
            elif pos_dir == "short":
                if current_close <= mean_price or sentiment <= -0.15:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_normalized",
                            "sentiment": sentiment,
                            "close": current_close,
                            "mean_price": mean_price,
                            "rsi": rsi,
                        },
                    )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        highest_high = max(highs[-self.pullback_lookback:])
        lowest_low = min(lows[-self.pullback_lookback:])

        pullback_from_high = (highest_high - current_close) / highest_high
        rally_from_low = (current_close - lowest_low) / lowest_low

        prev_pullback = (highest_high - prev_close) / highest_high
        prev_rally = (prev_close - lowest_low) / lowest_low

        if sentiment < -0.30 and rsi < 42.0:
            if pullback_from_high >= self.pullback_pct and prev_pullback < self.pullback_pct:
                confidence = min(0.9, 0.5 + abs(sentiment) * 0.4)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=350.0,
                    take_profit_bps=550.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "fear_pullback_dip_entry",
                        "sentiment": sentiment,
                        "pullback_pct": pullback_from_high,
                        "highest_high": highest_high,
                        "rsi": rsi,
                        "close": current_close,
                    },
                )

        if sentiment > 0.30 and rsi > 58.0:
            if rally_from_low >= self.pullback_pct and prev_rally < self.pullback_pct:
                confidence = min(0.9, 0.5 + abs(sentiment) * 0.4)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=350.0,
                    take_profit_bps=550.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "euphoria_rally_fade_entry",
                        "sentiment": sentiment,
                        "rally_pct": rally_from_low,
                        "lowest_low": lowest_low,
                        "rsi": rsi,
                        "close": current_close,
                    },
                )

        return None