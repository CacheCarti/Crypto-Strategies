from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _ema(self, values, period: int) -> Optional[float]:
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
        closes = ctx.closes(self.ema_period + 5)
        if len(closes) < self.ema_period + 3:
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Feature reading
        fng = ctx.features.get("fear_greed_index", 50.0)

        # Market sentiment filter: stand down on violent sentiment extremes
        if fng < 25.0 or fng > 75.0:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "fng_extreme_stand_down", "fng": fng}
                )
            return None

        # Indicator calculations
        curr_ema = self._ema(closes, self.ema_period)
        prev_ema = self._ema(closes[:-1], self.ema_period)
        if curr_ema is None or prev_ema is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        ema_slope = (curr_ema - prev_ema) / prev_ema * 10000.0  # slope in bps
        slope_thresh = 1.5

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Exit if momentum reverses
            if pos_dir == "long" and (curr_close < curr_ema or ema_slope < -slope_thresh):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_momentum_lost", "close": curr_close, "ema20": curr_ema, "slope_bps": ema_slope}
                )
            elif pos_dir == "short" and (curr_close > curr_ema or ema_slope > slope_thresh):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_momentum_lost", "close": curr_close, "ema20": curr_ema, "slope_bps": ema_slope}
                )
            return None

        # Entry logic: cross or strong continuation with confirmed slope
        long_cross = prev_close <= prev_ema and curr_close > curr_ema
        long_momentum = curr_close > curr_ema and ema_slope > slope_thresh
        if (long_cross or long_momentum) and ema_slope > 0:
            confidence = min(0.85, 0.55 + max(0.0, ema_slope) / 20.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "sentiment_trend_long_trigger",
                    "fng": fng,
                    "close": curr_close,
                    "ema20": curr_ema,
                    "slope_bps": ema_slope
                }
            )

        short_cross = prev_close >= prev_ema and curr_close < curr_ema
        short_momentum = curr_close < curr_ema and ema_slope < -slope_thresh
        if (short_cross or short_momentum) and ema_slope < 0:
            confidence = min(0.85, 0.55 + max(0.0, -ema_slope) / 20.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "sentiment_trend_short_trigger",
                    "fng": fng,
                    "close": curr_close,
                    "ema20": curr_ema,
                    "slope_bps": ema_slope
                }
            )

        return None