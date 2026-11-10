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
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast = 20
        self.ema_slow = 50
        self.cooldown_period = 8
        self.last_exit_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard after exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Sentiment gate: moderate market psychology only (avoid extreme capitulation/euphoria reversals)
        fng = ctx.features.get("fear_greed_index", 50.0)
        if fng is None or fng <= 25.0 or fng >= 75.0:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "fng_extreme_sentiment_exit", "fng": fng})
            return None

        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0.0:
            return None

        ema20_now = self._ema(closes, self.ema_fast)
        ema20_prev = self._ema(closes[:-1], self.ema_fast)
        ema50_now = self._ema(closes, self.ema_slow)

        if ema20_now is None or ema20_prev is None or ema50_now is None:
            return None

        c_now = closes[-1]
        c_prev = closes[-2]
        ema20_rising = ema20_now > ema20_prev
        ema20_falling = ema20_now < ema20_prev

        # Active position management: exit if trend breaks
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if c_now < ema20_now and ema20_falling:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_trend_break", "price": c_now, "ema20": ema20_now})
            elif pos_dir == "short":
                if c_now > ema20_now and ema20_rising:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_trend_break", "price": c_now, "ema20": ema20_now})
            return None

        # Entry logic: strict event boundary (cross above/below EMA20 with momentum confirmation)
        bullish_cross = (c_prev <= ema20_prev) and (c_now > ema20_now)
        long_setup = bullish_cross and ema20_rising and (c_now > ema50_now)

        bearish_cross = (c_prev >= ema20_prev) and (c_now < ema20_now)
        short_setup = bearish_cross and ema20_falling and (c_now < ema50_now)

        atr_bps = (atr / c_now) * 10000.0
        sl_bps = max(150.0, min(350.0, atr_bps * 1.5))
        tp_bps = sl_bps * 2.0

        if long_setup:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "bullish_ema20_cross_fng_filtered",
                    "fng": fng,
                    "ema20": ema20_now,
                    "ema50": ema50_now,
                    "price": c_now,
                    "atr_bps": atr_bps
                }
            )

        if short_setup:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "bearish_ema20_cross_fng_filtered",
                    "fng": fng,
                    "ema20": ema20_now,
                    "ema50": ema50_now,
                    "price": c_now,
                    "atr_bps": atr_bps
                }
            )

        return None