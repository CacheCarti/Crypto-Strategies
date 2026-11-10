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
        self.ema_fast_period = 20
        self.ema_slow_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_trade_bar = -999

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        ema_curr = self._ema(closes, self.ema_fast_period)
        ema_prev = self._ema(closes[:-1], self.ema_fast_period)
        ema_trend = self._ema(closes, self.ema_slow_period)
        atr_val = self._atr(highs, lows, closes, self.atr_period)

        if ema_curr is None or ema_prev is None or ema_trend is None or atr_val is None:
            return None

        current_close = ctx.bar.close
        prev_close = closes[-2]
        fng = ctx.features.get("fear_greed_index", 50.0)

        # Check existing positions for regime-driven or trailing exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Stand down at sentiment extremes
            if fng <= 25.0 or fng >= 75.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "sentiment_extreme_stand_down",
                    "fng": fng,
                    "price": current_close
                })

            # EMA breakdown exit
            if pos_dir == "long" and current_close < ema_curr:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "long_ema_cross_exit",
                    "ema20": ema_curr,
                    "price": current_close
                })
            elif pos_dir == "short" and current_close > ema_curr:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "short_ema_cross_exit",
                    "ema20": ema_curr,
                    "price": current_close
                })

            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Sentiment gate: strictly between 25 and 75
        if fng <= 25.0 or fng >= 75.0:
            return None

        # Trend & Momentum conditions: trigger on crossing EMA20 with EMA slope alignment
        long_trigger = (prev_close <= ema_prev) and (current_close > ema_curr) and (ema_curr > ema_prev) and (current_close > ema_trend)
        short_trigger = (prev_close >= ema_prev) and (current_close < ema_curr) and (ema_curr < ema_prev) and (current_close < ema_trend)

        # Dynamic stop loss based on ATR
        atr_bps = (atr_val / current_close) * 10000.0
        sl_bps = max(150.0, min(350.0, atr_bps * 1.5))
        tp_bps = sl_bps * 2.0

        if long_trigger:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.75, stop_loss_bps=sl_bps, take_profit_bps=tp_bps, metadata={
                "reason": "sentiment_gated_momentum_long_cross",
                "fng": fng,
                "price": current_close,
                "ema20": ema_curr,
                "ema50": ema_trend,
                "atr_bps": atr_bps
            })
        elif short_trigger:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.75, stop_loss_bps=sl_bps, take_profit_bps=tp_bps, metadata={
                "reason": "sentiment_gated_momentum_short_cross",
                "fng": fng,
                "price": current_close,
                "ema20": ema_curr,
                "ema50": ema_trend,
                "atr_bps": atr_bps
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index