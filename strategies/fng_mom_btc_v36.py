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
        "warmup_bars": 55,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 20
        self.ema_slow_period = 50
        self.last_trade_bar = -999
        self.cooldown_bars = 6
        self.prev_ema_fast = None

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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)

        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        if ema_fast is None or ema_slow is None:
            return None

        prev_closes = closes[:-1]
        prev_ema_fast = self._ema(prev_closes, self.ema_fast_period)
        prev_ema_slow = self._ema(prev_closes, self.ema_slow_period)

        price = ctx.bar.close
        prev_price = prev_closes[-1]

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                if price < ema_fast and prev_price >= prev_ema_fast:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "exit_long_loss_of_fast_ema", "price": price, "ema_fast": ema_fast}
                    )
            elif direction == "short":
                if price > ema_fast and prev_price <= prev_ema_fast:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "exit_short_regained_fast_ema", "price": price, "ema_fast": ema_fast}
                    )
            return None

        # Post-exit cooldown gate
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Sentiment regime filter: eliminate extreme crowd mania or panic capitulation
        fng = ctx.features.get("fear_greed_index", 50.0)
        if not (25.0 <= fng <= 75.0):
            return None

        # Market regime filter: avoid choppy sideways crisis or meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Event boundary: exact bar crossing over fast EMA in the direction of the macro trend
        bullish_macro = ema_fast > ema_slow and ema_fast > (prev_ema_fast or ema_fast)
        bearish_macro = ema_fast < ema_slow and ema_fast < (prev_ema_fast or ema_fast)

        long_trigger = bullish_macro and (prev_price <= (prev_ema_fast or ema_fast)) and (price > ema_fast)
        short_trigger = bearish_macro and (prev_price >= (prev_ema_fast or ema_fast)) and (price < ema_fast)

        if long_trigger:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "sentiment_filtered_bullish_ema_cross",
                    "price": price,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "fng": fng,
                    "atr": atr or 0.0,
                }
            )

        if short_trigger:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "sentiment_filtered_bearish_ema_cross",
                    "price": price,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "fng": fng,
                    "atr": atr or 0.0,
                }
            )

        return None