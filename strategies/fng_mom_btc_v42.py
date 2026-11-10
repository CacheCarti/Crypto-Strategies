from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 20
        self.ema_slow_period = 50
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.last_entry_bar = -999

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard: enforce quiet period post exit or entry to prevent overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if ctx.bar_index - self.last_entry_bar < self.cooldown_bars and not ctx.has_position():
            return None

        # Feature gate: Sentiment must be moderate (avoid violent reversal regimes)
        fng = ctx.features.get("fear_greed_index", 50.0)
        sentiment_ok = 25.0 <= fng <= 75.0

        # Market regime filter: avoid crisis/meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "regime_exit_crisis", "regime": regime, "fng": fng}
                )
            return None

        ema20_curr = self._ema(closes, self.ema_fast_period)
        ema20_prev = self._ema(closes[:-1], self.ema_fast_period)
        ema50_curr = self._ema(closes, self.ema_slow_period)

        if ema20_curr is None or ema20_prev is None or ema50_curr is None:
            return None

        price = ctx.bar.close
        prev_price = closes[-2]
        ema20_rising = ema20_curr > ema20_prev
        ema20_falling = ema20_curr < ema20_prev

        # Active position management: trend divergence exit
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and (price < ema20_curr or not ema20_rising):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_momentum_break", "price": price, "ema20": ema20_curr, "fng": fng}
                )
            elif direction == "short" and (price > ema20_curr or not ema20_falling):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_momentum_break", "price": price, "ema20": ema20_curr, "fng": fng}
                )
            return None

        # Stand down at sentiment extremes
        if not sentiment_ok:
            return None

        # Trigger logic: Price crossing above/below EMA20 while EMA20 aligns with trend (EMA50 filter)
        long_cross = prev_price <= ema20_prev and price > ema20_curr
        short_cross = prev_price >= ema20_prev and price < ema20_curr

        long_cond = long_cross and ema20_rising and price > ema50_curr
        short_cond = short_cross and ema20_falling and price < ema50_curr

        if long_cond:
            self.last_entry_bar = ctx.bar_index
            conf = 0.7 + 0.1 * min(1.0, max(0.0, (fng - 50.0) / 25.0))
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bull_momentum_cross_sentiment_neutral",
                    "fng": fng,
                    "ema20": round(ema20_curr, 2),
                    "ema50": round(ema50_curr, 2),
                    "price": price
                }
            )

        if short_cond:
            self.last_entry_bar = ctx.bar_index
            conf = 0.7 + 0.1 * min(1.0, max(0.0, (50.0 - fng) / 25.0))
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bear_momentum_cross_sentiment_neutral",
                    "fng": fng,
                    "ema20": round(ema20_curr, 2),
                    "ema50": round(ema50_curr, 2),
                    "price": price
                }
            )

        return None