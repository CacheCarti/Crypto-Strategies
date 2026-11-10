from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGatedPullbackSol(Strategy):
    METADATA = {
        "name": "SentimentGatedPullbackSol",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 8
        self.last_exit_bar = -100

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        curr_close = closes[-1]
        sma_mean = self._sma(closes, 24)
        if sma_mean is None:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        regime = ctx.market.get("regime", "NORMAL")

        # Prior evidence failure fix: filter out hyper-stress/meltdown regimes where dip-buying fails catastrophically
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_regime_exit", "regime": regime})
            return None

        # Handle active position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion or sentiment normalization exit
            if pos_dir == "long":
                if curr_close >= sma_mean or sentiment > 0.1:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "long_take_profit_or_sentiment_norm",
                        "price": curr_close,
                        "sma": sma_mean,
                        "sentiment": sentiment
                    })
            elif pos_dir == "short":
                if curr_close <= sma_mean or sentiment < -0.1:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "short_take_profit_or_sentiment_norm",
                        "price": curr_close,
                        "sma": sma_mean,
                        "sentiment": sentiment
                    })
            return None

        # Entry logic: 3% pullback from recent extreme gated by fear/euphoria sentiment
        highest_high = max(highs[-self.lookback:])
        lowest_low = min(lows[-self.lookback:])

        # Long: sentiment fearful (<= -0.3) & 3% pullback from rolling peak
        if sentiment <= -0.3 and highest_high > 0:
            pullback_depth = (highest_high - curr_close) / highest_high
            if pullback_depth >= self.pullback_pct:
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=400.0,
                    take_profit_bps=500.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "fear_sentiment_dip_pullback",
                        "sentiment": sentiment,
                        "highest_high": highest_high,
                        "pullback_depth": pullback_depth,
                        "price": curr_close
                    }
                )

        # Short: sentiment euphoric (>= 0.3) & 3% rally from rolling trough
        if sentiment >= 0.3 and lowest_low > 0:
            rally_depth = (curr_close - lowest_low) / lowest_low
            if rally_depth >= self.pullback_pct:
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=400.0,
                    take_profit_bps=500.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "euphoria_sentiment_rally_fade",
                        "sentiment": sentiment,
                        "lowest_low": lowest_low,
                        "rally_depth": rally_depth,
                        "price": curr_close
                    }
                )

        return None