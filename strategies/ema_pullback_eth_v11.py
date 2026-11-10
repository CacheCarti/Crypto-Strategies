from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackVolAdaptive(Strategy):
    METADATA = {
        "name": "EthEmaPullbackVolAdaptive",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold intended
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14):
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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.slow_period + 5)
        highs = ctx.highs(self.atr_period + 5)
        lows = ctx.lows(self.atr_period + 5)

        if len(closes) < self.slow_period + 2:
            return None

        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if ema20 is None or ema50 is None or atr is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        # Calculate dynamic ATR-based SL/TP to solve the historical static-stop reject issue
        atr_bps = (atr / curr_close) * 10000.0
        dyn_sl_bps = max(200.0, min(500.0, atr_bps * 1.8))
        dyn_tp_bps = max(350.0, min(800.0, dyn_sl_bps * 1.7))

        pos_dir = ctx.position_direction()

        # Handle active position exit: cross against EMA20
        if ctx.has_position():
            if pos_dir == "long" and curr_close < ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_cross_below_ema20", "close": curr_close, "ema20": ema20}
                )
            elif pos_dir == "short" and curr_close > ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_cross_above_ema20", "close": curr_close, "ema20": ema20}
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter to avoid choppy crisis environments
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Long pullback setup:
        # 1. Macro uptrend: Price & EMA20 above EMA50
        # 2. Pullback & reclaim: Prior bar tested <= EMA20 (or dipped below), current bar closes firmly back above EMA20
        long_setup = (
            curr_close > ema50 and
            ema20 > ema50 and
            prev_low <= ema20 and
            prev_close <= ema20 * 1.002 and
            curr_close > ema20
        )

        if long_setup:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=dyn_sl_bps,
                take_profit_bps=dyn_tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_reclaim_long",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "atr_bps": atr_bps,
                    "dyn_sl_bps": dyn_sl_bps
                }
            )

        # Short pullback setup:
        # 1. Macro downtrend: Price & EMA20 below EMA50
        # 2. Pullback & reject: Prior bar tested >= EMA20, current bar closes back below EMA20
        short_setup = (
            curr_close < ema50 and
            ema20 < ema50 and
            prev_high >= ema20 and
            prev_close >= ema20 * 0.998 and
            curr_close < ema20
        )

        if short_setup:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=dyn_sl_bps,
                take_profit_bps=dyn_tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_reject_short",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "atr_bps": atr_bps,
                    "dyn_sl_bps": dyn_sl_bps
                }
            )

        return None