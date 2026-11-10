from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGatedSolPullback(Strategy):
    METADATA = {
        "name": "SentimentGatedSolPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 43200,  # ~12 hours swing target
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.cooldown_bars = 10
        self.last_exit_bar = -100
        self.pullback_pct = 0.035
        self.rsi_period = 14

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
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback:
            return None

        # Cooldown guard after exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Slow sentiment regime filter
        sent = ctx.features.get("twitter_sentiment", 0.0)
        curr_price = ctx.bar.close

        # Base price calculations
        max_high = max(highs[-self.lookback:])
        min_low = min(lows[-self.lookback:])
        mean_24 = sum(closes[-24:]) / 24.0
        rsi = self._rsi(closes, self.rsi_period)

        # Handle active position management (Mean reversion or sentiment normalization exits)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if curr_price >= mean_24 or sent > 0.1:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_normalized",
                            "price": curr_price,
                            "mean_24": mean_24,
                            "sentiment": sent,
                        },
                    )
            elif pos_dir == "short":
                if curr_price <= mean_24 or sent < -0.1:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_normalized",
                            "price": curr_price,
                            "mean_24": mean_24,
                            "sentiment": sent,
                        },
                    )
            return None

        # No active position: Evaluate high-conviction entries
        if rsi is None:
            return None

        # Long Setup: Extreme fear + meaningful dip from local peak + oversold momentum
        pullback = (max_high - curr_price) / max_high
        if sent <= -0.30 and pullback >= self.pullback_pct and rsi <= 40.0:
            confidence = min(0.9, 0.6 + abs(sent) * 0.3)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=650.0,
                horizon_seconds=43200,
                metadata={
                    "reason": "fear_sentiment_dip_pullback",
                    "sentiment": sent,
                    "pullback_pct": pullback,
                    "rsi": rsi,
                    "max_high": max_high,
                    "price": curr_price,
                },
            )

        # Short Setup: Extreme euphoria + meaningful surge above local trough + overbought momentum
        rally = (curr_price - min_low) / min_low
        if sent >= 0.30 and rally >= self.pullback_pct and rsi >= 60.0:
            confidence = min(0.9, 0.6 + abs(sent) * 0.3)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=650.0,
                horizon_seconds=43200,
                metadata={
                    "reason": "greed_sentiment_rally_exhaustion",
                    "sentiment": sent,
                    "rally_pct": rally,
                    "rsi": rsi,
                    "min_low": min_low,
                    "price": curr_price,
                },
            )

        return None