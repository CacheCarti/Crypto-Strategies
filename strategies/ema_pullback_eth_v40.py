from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackAdvanced(Strategy):
    METADATA = {
        "name": "EthEmaPullbackAdvanced",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
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
        closes = ctx.closes(70)
        highs = ctx.highs(70)
        lows = ctx.lows(70)

        if len(closes) < 60:
            return None

        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if ema20 is None or ema50 is None or ema20_prev is None or ema50_prev is None or atr is None:
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
                    metadata={"reason": "crisis_exit", "regime": regime}
                )
            return None

        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close < ema20 and prev_close >= ema20_prev:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_long_ema20_cross_under", "close": curr_close, "ema20": ema20}
                )
            elif direction == "short" and curr_close > ema20 and prev_close <= ema20_prev:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_short_ema20_cross_over", "close": curr_close, "ema20": ema20}
                )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        atr_bps = (atr / curr_close) * 10000.0 if curr_close > 0 else 0.0
        if atr_bps < 30.0:
            return None

        # Uptrend: EMA20 above EMA50 and slope is positive
        trend_up = (ema20 > ema50) and (ema50 >= ema50_prev)
        # Pullback: prior bar dipped into or below EMA20, current bar reclaims EMA20
        pullback_reclaim_long = (prev_low <= ema20_prev) and (curr_close > ema20) and (curr_close > prev_close)

        # Downtrend: EMA20 below EMA50 and slope is negative
        trend_down = (ema20 < ema50) and (ema50 <= ema50_prev)
        # Pullback: prior bar popped into or above EMA20, current bar closes back below EMA20
        pullback_reject_short = (prev_high >= ema20_prev) and (curr_close < ema20) and (curr_close < prev_close)

        if trend_up and pullback_reclaim_long:
            conf = min(0.85, 0.55 + (ema20 - ema50) / curr_close * 15.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "ema_pullback_reclaim_uptrend",
                    "ema20": round(ema20, 2),
                    "ema50": round(ema50, 2),
                    "close": round(curr_close, 2),
                    "atr_bps": round(atr_bps, 1)
                }
            )

        if trend_down and pullback_reject_short:
            conf = min(0.85, 0.55 + (ema50 - ema20) / curr_close * 15.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "ema_pullback_reject_downtrend",
                    "ema20": round(ema20, 2),
                    "ema50": round(ema50, 2),
                    "close": round(curr_close, 2),
                    "atr_bps": round(atr_bps, 1)
                }
            )

        return None