from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentMomentumTrend(Strategy):
    METADATA = {
        "name": "Sentiment Momentum Trend",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 43200,
        "warmup_bars": 55,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 20
        self.slow_ema_period = 50
        self.rsi_period = 14
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.slow_ema_period + 10)
        if len(closes) < self.slow_ema_period + 2:
            return None

        # Stand down in crisis regime
        if ctx.regime == "crisis":
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))

        ema_fast_curr = self._ema(closes, self.fast_ema_period)
        ema_fast_prev = self._ema(closes[:-1], self.fast_ema_period)
        ema_slow_curr = self._ema(closes, self.slow_ema_period)
        rsi_val = self._rsi(closes, self.rsi_period)

        if ema_fast_curr is None or ema_fast_prev is None or ema_slow_curr is None or rsi_val is None:
            return None

        close_curr = closes[-1]
        close_prev = closes[-2]
        fast_slope = ema_fast_curr - ema_fast_prev

        # Active Position Management
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                # Exit if fast EMA crosses below slow EMA or extreme euphoria exhaustion
                if ema_fast_curr < ema_slow_curr or fg_index >= 80.0 or rsi_val > 78.0:
                    reason = "trend_exhaustion" if rsi_val > 78.0 else ("extreme_greed" if fg_index >= 80.0 else "ema_bear_cross")
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": reason,
                            "price": round(close_curr, 2),
                            "ema20": round(ema_fast_curr, 2),
                            "ema50": round(ema_slow_curr, 2),
                            "fear_greed": fg_index,
                            "rsi": round(rsi_val, 2),
                        },
                    )
            elif direction == "short":
                # Exit if fast EMA crosses above slow EMA or extreme fear capitulation
                if ema_fast_curr > ema_slow_curr or fg_index <= 20.0 or rsi_val < 22.0:
                    reason = "trend_exhaustion" if rsi_val < 22.0 else ("extreme_fear" if fg_index <= 20.0 else "ema_bull_cross")
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": reason,
                            "price": round(close_curr, 2),
                            "ema20": round(ema_fast_curr, 2),
                            "ema50": round(ema_slow_curr, 2),
                            "fear_greed": fg_index,
                            "rsi": round(rsi_val, 2),
                        },
                    )
            return None

        # Hard multi-bar cooldown after exit or previous entry to prevent chop
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Sentiment gate: strictly between 28 and 72 (avoid violent reversal extremes)
        if fg_index < 28.0 or fg_index > 72.0:
            return None

        # Long Entry: Price crosses above rising EMA20, confirmed by EMA20 > EMA50 and healthy RSI momentum
        bullish_cross = (close_prev <= ema_fast_prev) and (close_curr > ema_fast_curr)
        bullish_trend = ema_fast_curr > ema_slow_curr and fast_slope > 0.0
        if bullish_cross and bullish_trend and (48.0 <= rsi_val <= 65.0):
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_filtered_ema_bull_cross",
                    "price": round(close_curr, 2),
                    "ema20": round(ema_fast_curr, 2),
                    "ema50": round(ema_slow_curr, 2),
                    "fast_slope": round(fast_slope, 4),
                    "fear_greed": fg_index,
                    "rsi": round(rsi_val, 2),
                },
            )

        # Short Entry: Price crosses below falling EMA20, confirmed by EMA20 < EMA50 and healthy RSI momentum
        bearish_cross = (close_prev >= ema_fast_prev) and (close_curr < ema_fast_curr)
        bearish_trend = ema_fast_curr < ema_slow_curr and fast_slope < 0.0
        if bearish_cross and bearish_trend and (35.0 <= rsi_val <= 52.0):
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_filtered_ema_bear_cross",
                    "price": round(close_curr, 2),
                    "ema20": round(ema_fast_curr, 2),
                    "ema50": round(ema_slow_curr, 2),
                    "fast_slope": round(fast_slope, 4),
                    "fear_greed": fg_index,
                    "rsi": round(rsi_val, 2),
                },
            )

        return None