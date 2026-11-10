from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 20
        self.ema_trend_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(70)
        highs = ctx.highs(70)
        lows = ctx.lows(70)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Sentiment gate: moderate regime (avoid extreme reversal zones)
        fng = ctx.features.get("fear_greed_index", 50.0)
        if not (28.0 <= fng <= 72.0):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "fng_extreme_exit", "fng": fng}
                )
            return None

        ema20_curr = self._ema(closes, self.ema_fast_period)
        ema20_prev = self._ema(closes[:-1], self.ema_fast_period)
        ema50 = self._ema(closes, self.ema_trend_period)

        if ema20_curr is None or ema20_prev is None or ema50 is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        ema20_slope_bps = ((ema20_curr - ema20_prev) / ema20_prev) * 10000.0

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr == 0:
            return None

        # Volatility state confirmation: normalize ATR as bps of price
        atr_bps = (atr / curr_close) * 10000.0
        # Require reasonable active movement but reject chaotic volatility spikes (>500 bps/hr)
        if not (40.0 <= atr_bps <= 450.0):
            return None

        # Existing position exit rules
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and (curr_close < ema20_curr or ema20_slope_bps < -3.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_momentum_loss",
                        "close": curr_close,
                        "ema20": ema20_curr,
                        "slope_bps": ema20_slope_bps
                    }
                )
            elif direction == "short" and (curr_close > ema20_curr or ema20_slope_bps > 3.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_momentum_loss",
                        "close": curr_close,
                        "ema20": ema20_curr,
                        "slope_bps": ema20_slope_bps
                    }
                )
            return None

        # Entry logic: Fresh breakout/crossover into momentum state with EMA50 alignment
        # Long: Price crosses above EMA20, EMA20 is above EMA50 and rising decisively
        long_cross = (prev_close <= ema20_prev) and (curr_close > ema20_curr)
        if long_cross and (ema20_curr > ema50) and (ema20_slope_bps > 4.0):
            confidence = min(0.85, 0.55 + (ema20_slope_bps / 50.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_filtered_ema_breakout_long",
                    "fng": fng,
                    "ema20_slope_bps": ema20_slope_bps,
                    "atr_bps": atr_bps,
                    "close": curr_close,
                    "ema50": ema50
                }
            )

        # Short: Price crosses below EMA20, EMA20 is below EMA50 and falling decisively
        short_cross = (prev_close >= ema20_prev) and (curr_close < ema20_curr)
        if short_cross and (ema20_curr < ema50) and (ema20_slope_bps < -4.0):
            confidence = min(0.85, 0.55 + (abs(ema20_slope_bps) / 50.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_filtered_ema_breakout_short",
                    "fng": fng,
                    "ema20_slope_bps": ema20_slope_bps,
                    "atr_bps": atr_bps,
                    "close": curr_close,
                    "ema50": ema50
                }
            )

        return None