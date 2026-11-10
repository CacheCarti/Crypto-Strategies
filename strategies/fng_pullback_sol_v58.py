from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentEventPullback(Strategy):
    METADATA = {
        "name": "SolSentimentEventPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 14
        self.last_exit_bar = -100

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        # Hard cooldown between events to prevent overtrading and fee drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        current_close = closes[-1]
        sma_val = self._sma(closes, 20)
        sentiment = float(ctx.features.get("twitter_sentiment", 0.0))

        # Position management: explicit mean-reversion or sentiment normalization exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if (sma_val is not None and current_close >= sma_val) or sentiment >= 0.0:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_mean_reversion_or_sentiment_normalized",
                        "sentiment": sentiment,
                        "close": current_close,
                        "sma20": sma_val
                    })
            elif pos_dir == "short":
                if (sma_val is not None and current_close <= sma_val) or sentiment <= -0.0:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_mean_reversion_or_sentiment_normalized",
                        "sentiment": sentiment,
                        "close": current_close,
                        "sma20": sma_val
                    })
            return None

        # Price extremes over lookback window (excluding current bar)
        highest_prev = max(highs[-self.lookback - 1:-1])
        lowest_prev = min(lows[-self.lookback - 1:-1])

        # Formulate entries as distinct pullback events from extremes
        pullback_from_high = (highest_prev - current_close) / highest_prev
        bounce_from_low = (current_close - lowest_prev) / lowest_prev

        # Long: Fear regime + explicit pullback event of >= 3% from local peak
        if sentiment <= -0.30 and pullback_from_high >= self.pullback_pct:
            confidence = min(0.9, 0.6 + abs(sentiment) * 0.3)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_fear_pullback_dip_event",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "highest_ref": highest_prev,
                    "close": current_close
                }
            )

        # Short: Euphoric regime + explicit rally extension of >= 3% from local trough
        if sentiment >= 0.30 and bounce_from_low >= self.pullback_pct:
            confidence = min(0.9, 0.6 + sentiment * 0.3)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_euphoria_rally_stretch_event",
                    "sentiment": sentiment,
                    "bounce_pct": bounce_from_low,
                    "lowest_ref": lowest_prev,
                    "close": current_close
                }
            )

        return None