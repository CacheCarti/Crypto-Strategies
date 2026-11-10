from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 20
        self.ema_slow_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period):
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
        closes = ctx.closes(self.ema_slow_period + 5)
        if len(closes) < self.ema_slow_period + 2:
            return None

        # Manage cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        highs = ctx.highs(self.atr_period + 5)
        lows = ctx.lows(self.atr_period + 5)
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Calculate fast EMA and previous fast EMA to detect slope
        ema_fast_current = self._ema(closes, self.ema_fast_period)
        ema_fast_prev = self._ema(closes[:-1], self.ema_fast_period)
        ema_slow_current = self._ema(closes, self.ema_slow_period)

        if ema_fast_current is None or ema_fast_prev is None or ema_slow_current is None:
            return None

        current_close = ctx.bar.close
        prev_close = closes[-2]

        # Dynamic ATR-based Stop and Target (BPS)
        atr_bps = (atr / current_close) * 10000.0
        sl_bps = max(180.0, min(450.0, atr_bps * 1.8))
        tp_bps = max(350.0, min(900.0, atr_bps * 3.6))

        # Check existing positions for regime-driven exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Momentum breakdown exits
            if pos_dir == "long" and current_close < ema_fast_current:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_long_below_ema20", "close": current_close, "ema20": ema_fast_current}
                )
            elif pos_dir == "short" and current_close > ema_fast_current:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_short_above_ema20", "close": current_close, "ema20": ema_fast_current}
                )
            return None

        # Sentiment gate: avoid extreme greed (>75) or extreme fear (<25) where tops/bottoms form violently
        fng = ctx.features.get("fear_greed_index", 50.0)
        if fng < 25.0 or fng > 75.0:
            return None

        # Cross and momentum trigger conditions
        bullish_slope = ema_fast_current > ema_fast_prev
        bearish_slope = ema_fast_current < ema_fast_prev

        # Long: Price crosses above EMA20 or closes firmly above rising EMA20 & EMA50
        cross_above = prev_close <= ema_fast_prev and current_close > ema_fast_current
        trend_aligned_long = current_close > ema_fast_current > ema_slow_current and bullish_slope

        # Short: Price crosses below EMA20 or closes firmly below falling EMA20 & EMA50
        cross_below = prev_close >= ema_fast_prev and current_close < ema_fast_current
        trend_aligned_short = current_close < ema_fast_current < ema_slow_current and bearish_slope

        if cross_above and trend_aligned_long:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "sentiment_conditioned_ema20_breakout_long",
                    "fng": fng,
                    "ema20": ema_fast_current,
                    "ema50": ema_slow_current,
                    "atr_bps": atr_bps,
                    "close": current_close,
                }
            )

        if cross_below and trend_aligned_short:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "sentiment_conditioned_ema20_breakdown_short",
                    "fng": fng,
                    "ema20": ema_fast_current,
                    "ema50": ema_slow_current,
                    "atr_bps": atr_bps,
                    "close": current_close,
                }
            )

        return None