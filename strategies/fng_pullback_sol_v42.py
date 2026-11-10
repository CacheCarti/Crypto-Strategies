from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGatedPullbackSol(Strategy):
    METADATA = {
        "name": "SentimentGatedPullbackSol",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Regime gate to avoid choppy failure states noted in prior evidence
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_regime_exit", "crisis_score": crisis_score, "regime": regime}
                )
            return None

        current_close = closes[-1]
        mean_24 = self._sma(closes, 24)
        if mean_24 is None:
            return None

        # Slow sentiment filter: derived from Fear & Greed (-1.0 to 1.0)
        sentiment = ctx.features.get("twitter_sentiment", 0.0)

        # Handle active position exit
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean-reversion exit or sentiment normalization
            if direction == "long":
                if current_close >= mean_24 or sentiment > 0.15:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_tp_mean_reversion",
                            "close": current_close,
                            "mean_24": mean_24,
                            "sentiment": sentiment
                        }
                    )
            elif direction == "short":
                if current_close <= mean_24 or sentiment < -0.15:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_tp_mean_reversion",
                            "close": current_close,
                            "mean_24": mean_24,
                            "sentiment": sentiment
                        }
                    )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        rolling_high = max(highs[-self.lookback:])
        rolling_low = min(lows[-self.lookback:])

        pullback_from_high = (rolling_high - current_close) / rolling_high
        rally_from_low = (current_close - rolling_low) / rolling_low

        # Long entry: Fearful sentiment + distinct 3% pullback from local peak
        if sentiment < -0.30 and pullback_from_high >= self.pullback_pct:
            confidence = min(0.9, 0.6 + abs(sentiment) * 0.3)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_pullback_dip_buy",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "rolling_high": rolling_high,
                    "close": current_close
                }
            )

        # Short entry: Euphoric sentiment + distinct 3% rally from local trough
        if sentiment > 0.30 and rally_from_low >= self.pullback_pct:
            confidence = min(0.9, 0.6 + abs(sentiment) * 0.3)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "euphoria_rally_fade",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "rolling_low": rolling_low,
                    "close": current_close
                }
            )

        return None