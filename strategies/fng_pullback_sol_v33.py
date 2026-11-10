from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentPullback(Strategy):
    METADATA = {
        "name": "SolSentimentPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03       # 3% pullback/rally requirement
        self.sentiment_threshold = 0.25 # slightly relaxed from 0.3 for consistent sampling
        self.cooldown_bars = 6
        self.last_exit_bar = -100

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

        if len(closes) < self.lookback + 1:
            return None

        current_close = closes[-1]
        prior_close = closes[-2]
        sentiment = float(ctx.features.get("twitter_sentiment", 0.0))
        sma20 = self._sma(closes, 20)
        rsi = self._rsi(closes, 14)

        if sma20 is None or rsi is None:
            return None

        # Position management / mean-reversion exit
        if ctx.has_position():
            direction = ctx.position_direction()
            exit_signal = False
            exit_reason = ""

            if direction == "long":
                if current_close >= sma20:
                    exit_signal = True
                    exit_reason = "mean_reversion_to_sma20"
                elif sentiment >= 0.0:
                    exit_signal = True
                    exit_reason = "sentiment_normalized"
            elif direction == "short":
                if current_close <= sma20:
                    exit_signal = True
                    exit_reason = "mean_reversion_to_sma20"
                elif sentiment <= 0.0:
                    exit_signal = True
                    exit_reason = "sentiment_normalized"

            if exit_signal:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "sentiment": sentiment,
                        "close": current_close,
                        "sma20": sma20,
                        "rsi": rsi,
                    },
                )
            return None

        # Cooldown guard to avoid over-churning
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Reference high/low over the trailing lookback window (excluding current bar)
        recent_high = max(highs[-self.lookback - 1:-1])
        recent_low = min(lows[-self.lookback - 1:-1])

        pullback_from_high = (recent_high - current_close) / recent_high
        rally_from_low = (current_close - recent_low) / recent_low

        # Long Entry: Fear sentiment + distinct pullback + oversold reversal trigger
        if sentiment <= -self.sentiment_threshold:
            if pullback_from_high >= self.pullback_pct and rsi < 42:
                # Require price to stabilize (current close not breaking sharply below prior)
                if current_close >= prior_close * 0.998:
                    conf = min(0.85, 0.5 + abs(sentiment) * 0.4)
                    return ctx.signal(
                        "long",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "sentiment_fear_pullback_dip",
                            "sentiment": sentiment,
                            "pullback_pct": pullback_from_high,
                            "rsi": rsi,
                            "close": current_close,
                            "sma20": sma20,
                        },
                    )

        # Short Entry: Euphoric sentiment + extended rally + overbought exhaustion trigger
        elif sentiment >= self.sentiment_threshold:
            if rally_from_low >= self.pullback_pct and rsi > 58:
                # Require price to stall (current close not rocketing above prior)
                if current_close <= prior_close * 1.002:
                    conf = min(0.85, 0.5 + abs(sentiment) * 0.4)
                    return ctx.signal(
                        "short",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "sentiment_greed_rally_fade",
                            "sentiment": sentiment,
                            "rally_pct": rally_from_low,
                            "rsi": rsi,
                            "close": current_close,
                            "sma20": sma20,
                        },
                    )

        return None