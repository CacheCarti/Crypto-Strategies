from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 55,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 20
        self.ema_slow_period = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.min_ema_slope = 0.0005

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(60)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard to avoid churn and friction
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        fng = ctx.features.get("fear_greed_index", 50.0)
        # Stand down at sentiment extremes (violent mean-reversion zones)
        if fng <= 25.0 or fng >= 75.0:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "sentiment_extreme_stand_down", "fng": fng}
                )
            return None

        ema20 = self._ema(closes, self.ema_fast_period)
        ema20_prev = self._ema(closes[:-1], self.ema_fast_period)
        ema50 = self._ema(closes, self.ema_slow_period)

        if ema20 is None or ema20_prev is None or ema50 is None:
            return None

        price = ctx.bar.close
        ema_slope = (ema20 - ema20_prev) / ema20_prev

        # Long conditions: price above EMA20, EMA20 above EMA50, steep positive slope
        long_condition = (
            price > ema20
            and ema20 > ema50
            and ema_slope > self.min_ema_slope
        )

        # Short conditions: price below EMA20, EMA20 below EMA50, steep negative slope
        short_condition = (
            price < ema20
            and ema20 < ema50
            and ema_slope < -self.min_ema_slope
        )

        curr_dir = ctx.position_direction()

        # Dynamic confidence based on trend alignment and sentiment moderation
        sentiment_centrality = 1.0 - abs(fng - 50.0) / 25.0
        conf = max(0.4, min(0.9, 0.5 + 0.3 * sentiment_centrality))

        if long_condition and curr_dir != "long":
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bullish_momentum_within_neutral_sentiment",
                    "price": price,
                    "ema20": ema20,
                    "ema50": ema50,
                    "slope": ema_slope,
                    "fng": fng,
                }
            )

        if short_condition and curr_dir != "short":
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bearish_momentum_within_neutral_sentiment",
                    "price": price,
                    "ema20": ema20,
                    "ema50": ema50,
                    "slope": ema_slope,
                    "fng": fng,
                }
            )

        # Trend invalidation exit
        if curr_dir == "long" and price < ema20:
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={"reason": "price_lost_ema20_long_exit", "price": price, "ema20": ema20}
            )

        if curr_dir == "short" and price > ema20:
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={"reason": "price_reclaimed_ema20_short_exit", "price": price, "ema20": ema20}
            )

        return None