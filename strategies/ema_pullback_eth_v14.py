from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackRejection(Strategy):
    METADATA = {
        "name": "EthEmaPullbackRejection",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1.0 - k)
        return val

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 10)
        highs = ctx.highs(self.slow_period + 10)
        lows = ctx.lows(self.slow_period + 10)

        if len(closes) < self.slow_period + 5:
            return None

        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)

        if ema20_curr is None or ema50_curr is None or ema20_prev is None or ema50_prev is None:
            return None

        c_curr, c_prev = closes[-1], closes[-2]
        l_curr, h_curr = lows[-1], highs[-1]
        l_prev, h_prev = lows[-2], highs[-2]

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=1.0, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Manage existing position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and c_curr < ema20_curr and c_prev >= ema20_prev:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_long_ema20_cross_down",
                    "close": c_curr,
                    "ema20": ema20_curr
                })
            elif direction == "short" and c_curr > ema20_curr and c_prev <= ema20_prev:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_short_ema20_cross_up",
                    "close": c_curr,
                    "ema20": ema20_curr
                })
            return None

        # Gated by post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long pullback rejection: EMA20 > EMA50, previous bar breached below EMA20, current bar closes back firmly above EMA20
        long_trend = ema20_curr > ema50_curr and c_curr > ema50_curr
        long_pullback_touch = (l_prev <= ema20_prev) or (l_curr <= ema20_curr)
        long_trigger_cross = (c_prev <= ema20_prev) and (c_curr > ema20_curr)

        if long_trend and long_pullback_touch and long_trigger_cross:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_pullback_rejection_ema20",
                    "close": c_curr,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "low_prev": l_prev
                }
            )

        # Short pullback rejection: EMA20 < EMA50, previous bar breached above EMA20, current bar closes back firmly below EMA20
        short_trend = ema20_curr < ema50_curr and c_curr < ema50_curr
        short_pullback_touch = (h_prev >= ema20_prev) or (h_curr >= ema20_curr)
        short_trigger_cross = (c_prev >= ema20_prev) and (c_curr < ema20_curr)

        if short_trend and short_pullback_touch and short_trigger_cross:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_pullback_rejection_ema20",
                    "close": c_curr,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "high_prev": h_prev
                }
            )

        return None