from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentPullbackEvent(Strategy):
    METADATA = {
        "name": "SolSentimentPullbackEvent",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # ~8 hours swing
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        curr_close = closes[-1]
        prev_close = closes[-2]

        # Calculate mean reference (SMA 24)
        mean_price = sum(closes[-24:]) / 24.0

        # Position management / Exit triggers
        if ctx.has_position():
            direction = ctx.position_direction()
            should_flat = False
            exit_reason = ""

            # Check mean reversion or sentiment normalization
            if direction == "long":
                if curr_close >= mean_price:
                    should_flat = True
                    exit_reason = "mean_reversion_target_reached"
                elif sentiment >= 0.0:
                    should_flat = True
                    exit_reason = "sentiment_normalized"
            elif direction == "short":
                if curr_close <= mean_price:
                    should_flat = True
                    exit_reason = "mean_reversion_target_reached"
                elif sentiment <= 0.0:
                    should_flat = True
                    exit_reason = "sentiment_normalized"

            if should_flat:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "sentiment": sentiment,
                        "close": curr_close,
                        "mean_price": mean_price,
                    },
                )
            return None

        # Cooldown guard after exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Lookback boundaries over prior 48 bars (excluding current bar)
        prior_highs = highs[-self.lookback - 1 : -1]
        prior_lows = lows[-self.lookback - 1 : -1]
        rolling_high = max(prior_highs)
        rolling_low = min(prior_lows)

        long_threshold = rolling_high * (1.0 - self.pullback_pct)
        short_threshold = rolling_low * (1.0 + self.pullback_pct)

        # Discrete event boundary: cross below 3% threshold on this bar
        crossed_pullback = prev_close >= long_threshold and curr_close < long_threshold
        crossed_rally = prev_close <= short_threshold and curr_close > short_threshold

        rsi = self._rsi(closes, self.rsi_period)

        # Long setup: Fearful sentiment + distinct drop through the 3% boundary + oversold confirmation
        if sentiment < -0.30 and crossed_pullback and rsi < 42.0:
            confidence = min(0.9, 0.60 + abs(sentiment) * 0.25)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "sentiment_fear_pullback_cross",
                    "sentiment": sentiment,
                    "rolling_high": rolling_high,
                    "long_threshold": long_threshold,
                    "rsi": rsi,
                    "close": curr_close,
                },
            )

        # Short setup: Euphoric sentiment + distinct rally through the 3% boundary + overbought confirmation
        if sentiment > 0.30 and crossed_rally and rsi > 58.0:
            confidence = min(0.9, 0.60 + abs(sentiment) * 0.25)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "sentiment_euphoria_rally_cross",
                    "sentiment": sentiment,
                    "rolling_low": rolling_low,
                    "short_threshold": short_threshold,
                    "rsi": rsi,
                    "close": curr_close,
                },
            )

        return None