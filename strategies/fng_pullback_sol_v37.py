from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentMeanReversion(Strategy):
    METADATA = {
        "name": "SolSentimentMeanReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.bars_in_position = 0
        self.max_hold_bars = 18

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback:
            return None

        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)
        current_close = ctx.bar.close

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        mean_24 = self._sma(closes, 24)
        atr_val = self._atr(highs, lows, closes, 14)

        if mean_24 is None or atr_val is None:
            return None

        # Position management
        if ctx.has_position():
            self.bars_in_position += 1
            direction = ctx.position_direction()

            # Dynamic structural mean-reversion exit
            if direction == "long":
                if current_close >= mean_24 or sentiment > -0.05 or self.bars_in_position >= self.max_hold_bars:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_tp_or_sentiment_normalized",
                        "sentiment": sentiment,
                        "mean_24": mean_24,
                        "bars_held": self.bars_in_position
                    })
            elif direction == "short":
                if current_close <= mean_24 or sentiment < 0.05 or self.bars_in_position >= self.max_hold_bars:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_tp_or_sentiment_normalized",
                        "sentiment": sentiment,
                        "mean_24": mean_24,
                        "bars_held": self.bars_in_position
                    })
            return None

        # Check cooldown to prevent excessive trading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime safety: skip meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])

        # Measure pullback / rally depth
        pullback_pct = (recent_high - current_close) / recent_high if recent_high > 0 else 0.0
        rally_pct = (current_close - recent_low) / recent_low if recent_low > 0 else 0.0

        # Dynamic stop loss based on 2.5 * ATR (volatility stop)
        atr_bps = (atr_val / current_close) * 10000.0
        stop_bps = max(250.0, min(500.0, atr_bps * 2.5))
        tp_bps = stop_bps * 1.5

        # Long Entry: Fearful sentiment + 3% pullback from recent high
        if sentiment <= -0.30 and pullback_pct >= 0.03:
            confidence = min(0.9, 0.6 + abs(sentiment) * 0.3)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_sentiment_dip_pullback",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_pct,
                    "atr_bps": atr_bps,
                    "recent_high": recent_high
                }
            )

        # Short Entry: Euphoric sentiment + 3% rally from recent low
        if sentiment >= 0.30 and rally_pct >= 0.03:
            confidence = min(0.9, 0.6 + sentiment * 0.3)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "euphoria_sentiment_rally_fade",
                    "sentiment": sentiment,
                    "rally_pct": rally_pct,
                    "atr_bps": atr_bps,
                    "recent_low": recent_low
                }
            )

        return None