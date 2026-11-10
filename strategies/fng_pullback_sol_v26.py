from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentPullback(Strategy):
    METADATA = {
        "name": "SolSentimentPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 6
        self.last_exit_bar = -999

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

        if len(closes) < self.lookback:
            return None

        current_close = ctx.bar.close
        sma_mean = self._sma(closes, 24)
        if sma_mean is None:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Mean reversion or sentiment normalization exit
            if pos_dir == "long":
                if current_close >= sma_mean or sentiment > -0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_take_profit_mean_or_sentiment_norm",
                            "close": current_close,
                            "sma": sma_mean,
                            "sentiment": sentiment,
                        },
                    )
            elif pos_dir == "short":
                if current_close <= sma_mean or sentiment < 0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_take_profit_mean_or_sentiment_norm",
                            "close": current_close,
                            "sma": sma_mean,
                            "sentiment": sentiment,
                        },
                    )
            return None

        # Mandatory cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Rolling reference levels
        rolling_high = max(highs[-self.lookback:])
        rolling_low = min(lows[-self.lookback:])

        pullback_from_high = (rolling_high - current_close) / rolling_high
        rally_from_low = (current_close - rolling_low) / rolling_low

        # Entry triggers: buy fear dips, sell euphoric rallies
        # Bound maximum pullback to avoid catastrophic free-falls (falling knives)
        if sentiment <= -0.25 and self.pullback_pct <= pullback_from_high <= 0.08:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "fear_pullback_dip_buy",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "rolling_high": rolling_high,
                    "close": current_close,
                },
            )

        if sentiment >= 0.25 and self.pullback_pct <= rally_from_low <= 0.08:
            return ctx.signal(
                "short",
                confidence=0.70,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "euphoria_rally_short_fade",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "rolling_low": rolling_low,
                    "close": current_close,
                },
            )

        return None