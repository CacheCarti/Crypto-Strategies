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
        self.ema_fast = 20
        self.ema_trend = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_trend + 10)
        if len(closes) < self.ema_trend + 5:
            return None

        # Check explicit post-exit / post-entry cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if ctx.bar_index - self.last_entry_bar < self.cooldown_bars:
            return None

        # Sentiment gate: strictly between 25 and 75 (avoid violent extremes)
        fng = ctx.features.get("fear_greed_index", 50.0)
        if fng is None or fng <= 25.0 or fng >= 75.0:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "fng_extreme_exit", "fng": fng, "price": ctx.bar.close}
                )
            return None

        # EMA calculations for current and previous bar to detect transitions/slopes
        ema20_curr = self._ema(closes, self.ema_fast)
        ema20_prev = self._ema(closes[:-1], self.ema_fast)
        ema50_curr = self._ema(closes, self.ema_trend)

        if ema20_curr is None or ema20_prev is None or ema50_curr is None:
            return None

        price = ctx.bar.close
        prev_price = closes[-2]
        ema20_slope_bps = ((ema20_curr - ema20_prev) / ema20_prev) * 10000.0

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and (price < ema20_curr or ema20_slope_bps < -3.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_momentum_break", "price": price, "ema20": ema20_curr}
                )
            elif pos_dir == "short" and (price > ema20_curr or ema20_slope_bps > 3.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_momentum_break", "price": price, "ema20": ema20_curr}
                )
            return None

        # Strict event-driven entry on momentum trigger: fresh EMA20 cross + EMA slope confirmation
        bull_cross = (prev_price <= ema20_prev) and (price > ema20_curr)
        bull_confirmed = (price > ema50_curr) and (ema20_slope_bps > 4.0)

        bear_cross = (prev_price >= ema20_prev) and (price < ema20_curr)
        bear_confirmed = (price < ema50_curr) and (ema20_slope_bps < -4.0)

        if bull_cross and bull_confirmed:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bull_cross_slope_fng_normal",
                    "fng": fng,
                    "ema20": round(ema20_curr, 2),
                    "ema50": round(ema50_curr, 2),
                    "slope_bps": round(ema20_slope_bps, 2),
                    "price": price
                }
            )

        if bear_cross and bear_confirmed:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bear_cross_slope_fng_normal",
                    "fng": fng,
                    "ema20": round(ema20_curr, 2),
                    "ema50": round(ema50_curr, 2),
                    "slope_bps": round(ema20_slope_bps, 2),
                    "price": price
                }
            )

        return None