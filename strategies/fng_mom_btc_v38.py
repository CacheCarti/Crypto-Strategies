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
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_trade_bar = -999

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + 5)
        if len(closes) < self.ema_period + 5:
            return None

        highs = ctx.highs(self.atr_period + 2)
        lows = ctx.lows(self.atr_period + 2)
        atr = self._atr(highs, lows, ctx.closes(self.atr_period + 2), self.atr_period)
        if atr is None or atr == 0.0:
            return None

        # Filter out extreme crisis regimes (prior evidence showed OOS failures during violent tail events)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_crisis_regime", "regime": regime}
                )
            return None

        fng = ctx.features.get("fear_greed_index", 50.0)
        curr_price = ctx.bar.close
        prev_price = closes[-2]

        ema_curr = self._ema(closes, self.ema_period)
        ema_prev = self._ema(closes[:-1], self.ema_period)
        if ema_curr is None or ema_prev is None:
            return None

        ema_slope = ema_curr - ema_prev

        # Active position management: exit if trend structurally breaks
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_price < ema_curr and ema_slope < 0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_trend_break",
                        "fng": fng,
                        "ema20": ema_curr,
                        "price": curr_price,
                    }
                )
            if pos_dir == "short" and curr_price > ema_curr and ema_slope > 0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_trend_break",
                        "fng": fng,
                        "ema20": ema_curr,
                        "price": curr_price,
                    }
                )
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Gate on non-extreme sentiment (25 < fear_greed < 75)
        if not (25.0 < fng < 75.0):
            return None

        # Price momentum trigger: crossover or retest confirmation
        atr_buffer = 0.15 * atr
        long_trigger = (curr_price > ema_curr + atr_buffer) and (prev_price <= ema_prev + atr_buffer or ema_slope > 0) and (ema_slope > 0)
        short_trigger = (curr_price < ema_curr - atr_buffer) and (prev_price >= ema_prev - atr_buffer or ema_slope < 0) and (ema_slope < 0)

        # Confidence scaled by sentiment alignment within non-extreme band
        if long_trigger:
            conf = 0.55 + 0.35 * max(0.0, (fng - 50.0) / 25.0) if fng >= 50.0 else 0.55
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(0.9, conf),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_trend_continuation_fng_gate",
                    "fng": fng,
                    "ema20": ema_curr,
                    "ema_slope": ema_slope,
                    "price": curr_price,
                    "atr": atr,
                }
            )

        if short_trigger:
            conf = 0.55 + 0.35 * max(0.0, (50.0 - fng) / 25.0) if fng <= 50.0 else 0.55
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=min(0.9, conf),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_trend_continuation_fng_gate",
                    "fng": fng,
                    "ema20": ema_curr,
                    "ema_slope": ema_slope,
                    "price": curr_price,
                    "atr": atr,
                }
            )

        return None