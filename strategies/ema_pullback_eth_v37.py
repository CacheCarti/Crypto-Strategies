from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackAdvanced(Strategy):
    METADATA = {
        "name": "EthEmaPullbackAdvanced",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 4
        self.last_exit_bar = -100

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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        if ema20 is None or ema50 is None:
            return None

        # Prior bar EMA values
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)
        if ema20_prev is None or ema50_prev is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        curr_high = highs[-1]
        prev_low = lows[-2]
        prev_high = highs[-2]

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_exit", "regime": regime, "close": curr_close}
                )
            return None

        # Exit conditions when holding a position
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close < ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_cross_below_ema20", "close": curr_close, "ema20": ema20}
                )
            elif direction == "short" and curr_close > ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_cross_above_ema20", "close": curr_close, "ema20": ema20}
                )
            return None

        # Cooldown guard for new entries
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Long Setup: Trend established (EMA20 > EMA50, price > EMA50), pullback touched EMA20, now closing back above EMA20
        bull_trend = ema20 > ema50 and curr_close > ema50
        pullback_long = (curr_low <= ema20 or prev_low <= ema20_prev) and curr_close > ema20 and prev_close <= ema20_prev

        if bull_trend and pullback_long:
            sl_bps = max(150.0, min(350.0, (atr / curr_close) * 1.5 * 10000.0))
            tp_bps = sl_bps * 2.0
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_bullish_reclaim",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "atr": atr,
                }
            )

        # Short Setup: Downtrend established (EMA20 < EMA50, price < EMA50), bounce touched EMA20, now closing back below EMA20
        bear_trend = ema20 < ema50 and curr_close < ema50
        pullback_short = (curr_high >= ema20 or prev_high >= ema20_prev) and curr_close < ema20 and prev_close >= ema20_prev

        if bear_trend and pullback_short:
            sl_bps = max(150.0, min(350.0, (atr / curr_close) * 1.5 * 10000.0))
            tp_bps = sl_bps * 2.0
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_bearish_reclaim",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "atr": atr,
                }
            )

        return None