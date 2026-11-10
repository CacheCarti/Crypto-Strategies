from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackEvent(Strategy):
    METADATA = {
        "name": "EthEmaPullbackEvent",
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
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 5)
        lows = ctx.lows(5)
        highs = ctx.highs(5)

        if len(closes) < self.slow_period + 2:
            return None

        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)

        if ema20_curr is None or ema50_curr is None or ema20_prev is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close < ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_below_ema20", "close": curr_close, "ema20": ema20_curr}
                )
            elif pos_dir == "short" and curr_close > ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_above_ema20", "close": curr_close, "ema20": ema20_curr}
                )
            return None

        # Check cooldown to enforce strict event boundaries and avoid overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Discrete trigger: event boundary requires prior bar or intraday dip below EMA20 and clean reclaim
        # Long setup: Structural trend is up (EMA20 > EMA50, close > EMA50), pullback touched EMA20, now closed above
        long_structural = curr_close > ema50_curr and ema20_curr > ema50_curr
        long_pullback_event = (prev_close <= ema20_prev or lows[-1] <= ema20_curr) and curr_close > ema20_curr

        if long_structural and long_pullback_event:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=250.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "ema20_pullback_reclaim_bull",
                    "close": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr
                }
            )

        # Short setup: Structural trend is down (EMA20 < EMA50, close < EMA50), pullback touched EMA20, now closed below
        short_structural = curr_close < ema50_curr and ema20_curr < ema50_curr
        short_pullback_event = (prev_close >= ema20_prev or highs[-1] >= ema20_curr) and curr_close < ema20_curr

        if short_structural and short_pullback_event:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=250.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "ema20_pullback_reclaim_bear",
                    "close": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr
                }
            )

        return None