from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 20
        self.ema_slow_period = 50
        self.cooldown_bars = 8
        self.last_exit_bar = -100

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
        closes = ctx.closes(self.METADATA["warmup_bars"] + 2)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        fng = float(ctx.features.get("fear_greed_index", 50.0))
        # Filter: avoid extreme sentiment regimes where violent mean-reversions occur
        if fng < 25.0 or fng > 75.0:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "extreme_sentiment_exit", "fear_greed": fng}
                )
            return None

        ema20_curr = self._ema(closes, self.ema_fast_period)
        ema20_prev = self._ema(closes[:-1], self.ema_fast_period)
        ema50 = self._ema(closes, self.ema_slow_period)

        if ema20_curr is None or ema20_prev is None or ema50 is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        ema20_slope = (ema20_curr - ema20_prev) / ema20_prev
        pos_dir = ctx.position_direction()

        # Position management
        if pos_dir == "long":
            if curr_close < ema20_curr or ema20_slope < 0:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "long_momentum_lost", "close": curr_close, "ema20": ema20_curr}
                )
            return None

        if pos_dir == "short":
            if curr_close > ema20_curr or ema20_slope > 0:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "short_momentum_lost", "close": curr_close, "ema20": ema20_curr}
                )
            return None

        # Trigger event boundaries: price crosses EMA20 in direction of slope and macro EMA50
        bull_cross = prev_close <= ema20_prev and curr_close > ema20_curr
        bear_cross = prev_close >= ema20_prev and curr_close < ema20_curr

        if bull_cross and ema20_slope > 0.0005 and curr_close > ema50:
            conf = min(0.5 + abs(ema20_slope) * 50.0, 0.9)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bull_cross_momentum_aligned",
                    "close": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50,
                    "slope_bps": ema20_slope * 10000,
                    "fear_greed": fng
                }
            )

        if bear_cross and ema20_slope < -0.0005 and curr_close < ema50:
            conf = min(0.5 + abs(ema20_slope) * 50.0, 0.9)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bear_cross_momentum_aligned",
                    "close": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50,
                    "slope_bps": ema20_slope * 10000,
                    "fear_greed": fng
                }
            )

        return None