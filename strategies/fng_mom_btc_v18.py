from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 20
        self.ema_slow_period = 50
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.fng_min = 25.0
        self.fng_max = 75.0

    def _ema(self, values, period: int) -> Optional[float]:
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Check market regime gate: avoid crisis/meltdown where momentum chops
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_regime_crisis", "crisis_score": crisis_score, "regime": regime}
                )
            return None

        ema20_curr = self._ema(closes, self.ema_fast_period)
        ema20_prev = self._ema(closes[:-1], self.ema_fast_period)
        ema50 = self._ema(closes, self.ema_slow_period)

        if ema20_curr is None or ema20_prev is None or ema50 is None:
            return None

        current_price = ctx.bar.close
        fng = float(ctx.features.get("fear_greed_index", 50.0))

        # Check exit conditions for open positions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if current_price < ema20_curr or fng > self.fng_max:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "exit_long_trend_break_or_extreme_greed",
                            "price": current_price,
                            "ema20": ema20_curr,
                            "fng": fng
                        }
                    )
            elif pos_dir == "short":
                if current_price > ema20_curr or fng < self.fng_min:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "exit_short_trend_break_or_extreme_fear",
                            "price": current_price,
                            "ema20": ema20_curr,
                            "fng": fng
                        }
                    )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Sentiment gate: only trend-trade when sentiment is between 25 and 75
        if not (self.fng_min <= fng <= self.fng_max):
            return None

        ema20_slope = (ema20_curr - ema20_prev) / ema20_prev
        prev_close = closes[-2]

        # Long Setup: Fresh breakout/continuation above EMA20, EMA20 > EMA50, rising slope
        if current_price > ema20_curr and ema20_curr > ema50 and ema20_slope > 0.0003:
            if prev_close <= ema20_prev or current_price > closes[-2]:
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_gated_momentum_long",
                        "price": current_price,
                        "ema20": ema20_curr,
                        "ema50": ema50,
                        "ema20_slope": ema20_slope,
                        "fng": fng
                    }
                )

        # Short Setup: Fresh breakdown/continuation below EMA20, EMA20 < EMA50, falling slope
        if current_price < ema20_curr and ema20_curr < ema50 and ema20_slope < -0.0003:
            if prev_close >= ema20_prev or current_price < closes[-2]:
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_gated_momentum_short",
                        "price": current_price,
                        "ema20": ema20_curr,
                        "ema50": ema50,
                        "ema20_slope": ema20_slope,
                        "fng": fng
                    }
                )

        return None