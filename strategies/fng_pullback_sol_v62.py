from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentGatedPullback(Strategy):
    METADATA = {
        "name": "SolSentimentGatedPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 14400,  # ~4 hours on 1h bars
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.pullback_window = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 6
        self.last_exit_bar = -999

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

        # Mandatory cooldown to prevent overtrading and fee drag
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        sma_fast = self._sma(closes, 20)
        sentiment = ctx.features.get("twitter_sentiment", 0.0)

        # Failure mode addressed: family fng_pullback_sol failed in extreme crisis regimes.
        # Add strict regime gating to only trade when markets are orderly or high vol, not meltdown/crisis.
        market_regime = ctx.market.get("regime", "NORMAL").upper()
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "crisis_exit", "regime": market_regime, "crisis_score": crisis_score}
                )
            return None

        # Handle active position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion or sentiment normalization exit
            if pos_dir == "long":
                if (sma_fast is not None and current_price >= sma_fast) or sentiment > 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={"reason": "long_mean_reversion_or_sentiment_normalized", "price": current_price, "sma": sma_fast, "sentiment": sentiment}
                    )
            elif pos_dir == "short":
                if (sma_fast is not None and current_price <= sma_fast) or sentiment < 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={"reason": "short_mean_reversion_or_sentiment_normalized", "price": current_price, "sma": sma_fast, "sentiment": sentiment}
                    )
            return None

        # Entry logic: 48-bar extremes and sentiment gating
        high_ref = max(highs[-self.pullback_window:])
        low_ref = min(lows[-self.pullback_window:])

        pullback_from_high = (high_ref - current_price) / high_ref if high_ref > 0 else 0.0
        bounce_from_low = (current_price - low_ref) / low_ref if low_ref > 0 else 0.0

        # Long: 3% pullback from recent 48-bar high while sentiment is fearful (below -0.3)
        if sentiment < -0.30 and pullback_from_high >= self.pullback_pct:
            confidence = min(0.9, 0.6 + abs(sentiment) * 0.2)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_fear_pullback_buy",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "ref_high": high_ref,
                    "price": current_price
                }
            )

        # Short: 3% rally from recent 48-bar low while sentiment is euphoric (above +0.3)
        if sentiment > 0.30 and bounce_from_low >= self.pullback_pct:
            confidence = min(0.9, 0.6 + sentiment * 0.2)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_euphoria_rally_fade",
                    "sentiment": sentiment,
                    "rally_pct": bounce_from_low,
                    "ref_low": low_ref,
                    "price": current_price
                }
            )

        return None