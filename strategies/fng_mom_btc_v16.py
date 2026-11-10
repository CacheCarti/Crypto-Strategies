from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # 6 hours target hold on 1h bars
        "warmup_bars": 55,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 20
        self.trend_ema_period = 50
        self.cooldown_bars = 4
        self.last_exit_bar = -999

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
        closes = ctx.closes(self.trend_ema_period + 5)
        if len(closes) < self.trend_ema_period + 2:
            return None

        # Check sentiment extreme filter (25 to 75 avoids violent turnaround regimes)
        fng = float(ctx.features.get("fear_greed_index", 50))
        if fng < 25.0 or fng > 75.0:
            # Stand down at extremes; if currently holding, signal flat to protect gains/capital
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "fng_extreme_stand_down", "fng": fng, "price": ctx.bar.close}
                )
            return None

        # Calculate indicators
        ema20_curr = self._ema(closes, self.fast_ema_period)
        ema20_prev = self._ema(closes[:-1], self.fast_ema_period)
        ema50 = self._ema(closes, self.trend_ema_period)

        if ema20_curr is None or ema20_prev is None or ema50 is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if curr_close < ema20_curr or ema20_curr < ema20_prev:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "exit_ema20_momentum_lost",
                            "close": curr_close,
                            "ema20": ema20_curr,
                            "fng": fng
                        }
                    )
            elif pos_dir == "short":
                if curr_close > ema20_curr or ema20_curr > ema20_prev:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "exit_ema20_momentum_lost",
                            "close": curr_close,
                            "ema20": ema20_curr,
                            "fng": fng
                        }
                    )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic: strict event boundary via fresh crossover / bounce over EMA20 aligned with EMA50 macro trend
        long_event = (
            prev_close <= ema20_prev and
            curr_close > ema20_curr and
            ema20_curr > ema20_prev and
            curr_close > ema50
        )

        short_event = (
            prev_close >= ema20_prev and
            curr_close < ema20_curr and
            ema20_curr < ema20_prev and
            curr_close < ema50
        )

        if long_event:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "long_momentum_ema20_cross_aligned",
                    "fng": fng,
                    "close": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50
                }
            )

        if short_event:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "short_momentum_ema20_cross_aligned",
                    "fng": fng,
                    "close": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50
                }
            )

        return None