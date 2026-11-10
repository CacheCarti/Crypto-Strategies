from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slope_lookback = 3
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _ema_series(self, values: list, period: int) -> list:
        if len(values) < period:
            return []
        k = 2.0 / (period + 1)
        res = [sum(values[:period]) / period]
        for v in values[period:]:
            res.append(v * k + res[-1] * (1.0 - k))
        return res

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Sentiment regime gating: avoid euphoric blowoffs or capitulation knives
        fng = ctx.features.get("fear_greed_index", 50.0)
        in_trend_regime = 25.0 <= fng <= 75.0

        ema_vals = self._ema_series(closes, self.fast_period)
        if len(ema_vals) < self.slope_lookback + 2:
            return None

        curr_ema = ema_vals[-1]
        prev_ema = ema_vals[-1 - self.slope_lookback]
        ema_slope = (curr_ema - prev_ema) / prev_ema

        price = ctx.bar.close
        prev_price = closes[-2]
        ema_prev_bar = ema_vals[-2]

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic if sentiment reaches extreme reversal territory or trend breaks
        if has_pos:
            if not in_trend_regime:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "fng_extreme_sentiment_exit", "fng": fng, "price": price}
                )
            if pos_dir == "long" and price < curr_ema:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_trend_break_below_ema", "ema": curr_ema, "price": price}
                )
            elif pos_dir == "short" and price > curr_ema:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_trend_break_above_ema", "ema": curr_ema, "price": price}
                )
            return None

        # Gated from new entries during sentiment extremes
        if not in_trend_regime:
            return None

        # Entry triggers: Price crosses EMA20 in direction of slope confirmation
        # Long: Price crosses above EMA20 and EMA20 has an upward slope
        bullish_cross = prev_price <= ema_prev_bar and price > curr_ema
        if bullish_cross and ema_slope > 0.0005:
            # Scale confidence by slope and distance from extreme
            sentiment_quality = 1.0 - abs(fng - 50.0) / 25.0
            conf = min(0.9, max(0.55, 0.55 + 0.35 * sentiment_quality))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bullish_ema_cross_trend_filtered",
                    "fng": fng,
                    "ema20": curr_ema,
                    "slope": ema_slope,
                    "price": price
                }
            )

        # Short: Price crosses below EMA20 and EMA20 has a downward slope
        bearish_cross = prev_price >= ema_prev_bar and price < curr_ema
        if bearish_cross and ema_slope < -0.0005:
            sentiment_quality = 1.0 - abs(fng - 50.0) / 25.0
            conf = min(0.9, max(0.55, 0.55 + 0.35 * sentiment_quality))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bearish_ema_cross_trend_filtered",
                    "fng": fng,
                    "ema20": curr_ema,
                    "slope": ema_slope,
                    "price": price
                }
            )

        return None