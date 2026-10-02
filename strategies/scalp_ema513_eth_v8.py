from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EmaMomentumScalp(Strategy):
    METADATA = {
        "name": "EmaMomentumScalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 85.0,
        "declared_tp_bps": 170.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.slow_period = 30
        self.cooldown_bars = 25
        self.last_trade_bar = -999
        self.min_spread_bps = 8.0

    def _ema_series(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        res = [ema]
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
            res.append(ema)
        return res

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 10)
        if len(closes) < self.slow_period + 2:
            return None

        fast_series = self._ema_series(closes, self.fast_period)
        slow_series = self._ema_series(closes, self.slow_period)
        if not fast_series or not slow_series or len(fast_series) < 2 or len(slow_series) < 2:
            return None

        fast_curr = fast_series[-1]
        fast_prev = fast_series[-2]
        slow_curr = slow_series[-1]
        slow_prev = slow_series[-2]

        curr