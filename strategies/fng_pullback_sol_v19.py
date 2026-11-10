from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentAdaptivePullbackSol(Strategy):
    METADATA = {
        "name": "SentimentAdaptivePullbackSol",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # ~8h average swing hold
        "warmup_bars": 60,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.pullback_threshold = 0.035
        self.rsi_period = 14

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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

    def _sma(self, values, period):
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

        # Cooldown guard to avoid churn and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        curr_close = closes[-1]
        mean_price = self._sma(closes, self.lookback)
        rsi = self._rsi(closes, self.rsi_period)

        max_high = max(highs[-self.lookback:])
        min_low = min(lows[-self.lookback:])

        pullback_from_high = (max_high - curr_close) / max_high if max_high > 0 else 0.0
        rally_from_low = (curr_close - min_low) / min_low if min_low > 0 else 0.0

        # Existing position exit management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion exit or sentiment normalization
            if pos_dir == "long":
                if curr_close >= mean_price or sentiment > 0.15 or rsi > 65:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_normalization",
                            "close": curr_close,
                            "mean": mean_price,
                            "sentiment": sentiment,
                            "rsi": rsi,
                        },
                    )
            elif pos_dir == "short":
                if curr_close <= mean_price or sentiment < -0.15 or rsi < 35:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_normalization",
                            "close": curr_close,
                            "mean": mean_price,
                            "sentiment": sentiment,
                            "rsi": rsi,
                        },
                    )
            return None

        # Long entry: Fearful sentiment + substantial pullback + RSI stabilizing from oversold
        if sentiment < -0.25 and pullback_from_high >= self.pullback_threshold and rsi < 42:
            conf = min(0.9, 0.5 + abs(sentiment) * 0.5)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "sentiment_fear_dip_pullback",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "rsi": rsi,
                    "price": curr_close,
                    "anchor_high": max_high,
                },
            )

        # Short entry: Euphoric sentiment + extended rally + RSI showing exhaustion
        if sentiment > 0.25 and rally_from_low >= self.pullback_threshold and rsi > 58:
            conf = min(0.9, 0.5 + abs(sentiment) * 0.5)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "sentiment_greed_rally_fade",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "rsi": rsi,
                    "price": curr_close,
                    "anchor_low": min_low,
                },
            )

        return None