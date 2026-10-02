from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "Sentiment Conditioned Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 12
        self.slow_period = 26
        self.rsi_period = 14
        self.cooldown_bars = 10
        self.last_exit_bar = -100

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 20)
        if len(closes) < self.slow_period + 10:
            return None

        ema_fast_now = self._ema(closes, self.fast_period)
        ema_slow_now = self._ema(closes, self.slow_period)
        ema_fast_prev = self._ema(closes[:-1], self.fast_period)
        ema_slow_prev = self._ema(closes[:-1], self.slow_period)
        ema_slow_prior = self._ema(closes[:-3], self.slow_period)
        rsi_val = self._rsi(closes, self.rsi_period)

        if None in (ema_fast_now, ema_slow_now, ema_fast_prev, ema_slow_prev, ema_slow_prior, rsi_val):
            return None

        curr_close = closes[-1]
        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # Stand down at sentiment extremes (< 25 extreme fear, > 75 extreme greed)
        sentiment_ok = 25.0 <= fear_greed <= 75.0

        # Position exit management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if (curr_close < ema_slow_now and ema_fast_now < ema_fast_prev) or rsi_val >= 80.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_trend_break_or_exhaustion",
                            "close": curr_close,
                            "ema_slow": ema_slow_now,
                            "rsi": rsi_val,
                            "fear_greed": fear_greed,
                        },
                    )
            elif pos_dir == "short":
                if (curr_close > ema_slow_now and ema_fast_now > ema_fast_prev) or rsi_val <= 20.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_trend_break_or_exhaustion",
                            "close": curr_close,
                            "ema_slow": ema_slow_now,
                            "rsi": rsi_val,
                            "fear_greed": fear_greed,
                        },
                    )
            return None

        # Hard multi-bar cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if not sentiment_ok:
            return None

        # Slope checks over a multi-bar window
        ema_slow_rising = ema_slow_now > ema_slow_prior
        ema_slow_falling = ema_slow_now < ema_slow_prior

        # Strict crossover / transition triggers to avoid firing every bar
        bullish_cross = (ema_fast_prev <= ema_slow_prev) and (ema_fast_now > ema_slow_now)
        price_reclaim_bull = (closes[-2] <= ema_fast_prev) and (curr_close > ema_fast_now) and (ema_fast_now > ema_slow_now)

        if (bullish_cross or price_reclaim_bull) and ema_slow_rising:
            if 50.0 <= rsi_val <= 68.0 and curr_close > ema_slow_now:
                confidence = 0.7 if (40.0 <= fear_greed <= 65.0) else 0.55
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_filtered_bullish_cross_or_reclaim",
                        "close": curr_close,
                        "ema_fast": ema_fast_now,
                        "ema_slow": ema_slow_now,
                        "rsi": rsi_val,
                        "fear_greed": fear_greed,
                    },
                )

        bearish_cross = (ema_fast_prev >= ema_slow_prev) and (ema_fast_now < ema_slow_now)
        price_breakdown_bear = (closes[-2] >= ema_fast_prev) and (curr_close < ema_fast_now) and (ema_fast_now < ema_slow_now)

        if (bearish_cross or price_breakdown_bear) and ema_slow_falling:
            if 32.0 <= rsi_val <= 50.0 and curr_close < ema_slow_now:
                confidence = 0.7 if (35.0 <= fear_greed <= 60.0) else 0.55
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_filtered_bearish_cross_or_breakdown",
                        "close": curr_close,
                        "ema_fast": ema_fast_now,
                        "ema_slow": ema_slow_now,
                        "rsi": rsi_val,
                        "fear_greed": fear_greed,
                    },
                )

        return None