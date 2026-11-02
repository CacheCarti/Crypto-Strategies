from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class WideRangeContinuation(Strategy):
    """Wide-range-day continuation: when the last completed day's true range
    was the widest in N days, trade next-day continuation (Oxford Strat,
    42 futures markets / 39y). Optional same-direction-as-trend filter."""
    METADATA = {"name": "v3h_btc_wrc_40_3d_nf", "domain": "btc_usdc", "declared_sl_bps": 1500, "declared_tp_bps": 1800, "declared_hold_seconds": 259200, "warmup_bars": 20160}

    def initialize(self, ctx: BarContext) -> None:
        self.look_days = 40           # TR must be max over this many days
        self.hold_days = 3
        self.trend_filter = False       # bool: require 20d trend alignment
        self.side = "both"
        self.min_days = self.look_days + (20 if self.trend_filter else 0) + 5
        self._last_day = None

    def _daily(self, ctx):
        days = {}
        for b in ctx.bars:
            k = b.timestamp.date()
            if k not in days:
                days[k] = [b.open, b.high, b.low, b.close]
            else:
                days[k][1] = max(days[k][1], b.high)
                days[k][2] = min(days[k][2], b.low)
                days[k][3] = b.close
        keys = sorted(days)
        return [days[k] for k in keys]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        d = ctx.bar.timestamp.date()
        if d == self._last_day:
            return None
        self._last_day = d
        days = self._daily(ctx)
        if len(days) < self.min_days:
            return None
        if ctx.has_position():
            return None
        prev = days[-2]    # last COMPLETED day; today is days[-1] still forming
        tr = prev[1] - prev[2]
        if tr <= 0:
            return None
        window = days[-(self.look_days + 1):-1]
        if tr < max(x[1] - x[2] for x in window):
            return None
        move = prev[3] - prev[0]
        if move == 0:
            return None
        direction = "long" if move > 0 else "short"
        if direction == "short" and self.side == "long":
            return None
        if self.trend_filter:
            base = days[-21][3]
            if (direction == "long" and prev[3] < base) or \
               (direction == "short" and prev[3] > base):
                return None
        avg_tr = sum(x[1] - x[2] for x in window) / len(window)
        conf = min(0.8, 0.55 + max(0.0, tr / avg_tr - 1.2) * 0.1)
        return ctx.signal(direction, conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": "wide_range_cont", "tr_bps":
                       round(tr / prev[2] * 10000, 1)})
