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
        self.ema_period = 20
        self.cooldown_bars = 6
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
        closes = ctx.closes(50)
        if len(closes) < 30:
            return None

        fg_index = ctx.features.get("fear_greed_index", 50.0)
        if fg_index is None:
            fg_index = 50.0

        ema_now = self._ema(closes, self.ema_period)
        ema_prev = self._ema(closes[:-1], self.ema_period)
        if ema_now is None or ema_prev is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            should_exit = False
            exit_reason = ""

            if pos_dir == "long":
                if curr_close < ema_now:
                    should_exit = True
                    exit_reason = "loss_of_trend_long"
                elif fg_index >= 78.0:
                    should_exit = True
                    exit_reason = "sentiment_extreme_greed"
            elif pos_dir == "short":
                if curr_close > ema_now:
                    should_exit = True
                    exit_reason = "loss_of_trend_short"
                elif fg_index <= 22.0:
                    should_exit = True
                    exit_reason = "sentiment_extreme_fear"

            if should_exit:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": exit_reason,
                    "close": curr_close,
                    "ema20": ema_now,
                    "fear_greed": fg_index
                })
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if not (25.0 <= fg_index <= 75.0):
            return None

        ema_rising = ema_now > ema_prev
        ema_falling = ema_now < ema_prev

        long_cross = (prev_close <= ema_prev) and (curr_close > ema_now) and ema_rising
        short_cross = (prev_close >= ema_prev) and (curr_close < ema_now) and ema_falling

        if long_cross:
            confidence = 0.65 + min(max((fg_index - 50.0) / 100.0, 0.0), 0.15)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "sentiment_gated_ema20_breakout_long",
                    "close": curr_close,
                    "prev_close": prev_close,
                    "ema20": ema_now,
                    "ema20_prev": ema_prev,
                    "fear_greed": fg_index
                }
            )

        if short_cross:
            confidence = 0.65 + min(max((50.0 - fg_index) / 100.0, 0.0), 0.15)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "sentiment_gated_ema20_breakdown_short",
                    "close": curr_close,
                    "prev_close": prev_close,
                    "ema20": ema_now,
                    "ema20_prev": ema_prev,
                    "fear_greed": fg_index
                }
            )

        return None