from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class DailyTSMOM(Strategy):
    """Vol-normalized daily time-series momentum (Vilnius 2020-2025 spec:
    multi-horizon EMA momentum normalized by daily vol; 1-5d holds)."""
    METADATA = {"name": "v3h_btc_tsmom_40_8_5d_both", "domain": "btc_usdc", "declared_sl_bps": 1200, "declared_tp_bps": 1500, "declared_hold_seconds": 432000, "warmup_bars": 18720}

    def initialize(self, ctx: BarContext) -> None:
        self.look_days = 40           # momentum lookback in days
        self.z_thresh = 0.8               # |z| of cum return / (sigma*sqrt(L))
        self.hold_days = 5
        self.side = "both"              # both|long
        self.min_days = self.look_days + 5
        self.cooldown_bars = 288          # one signal evaluation per day
        self._last_day = None

    def _daily_closes(self, ctx):
        days = {}
        for b in ctx.bars:
            days[b.timestamp.date()] = b.close
        keys = sorted(days)
        return [days[k] for k in keys]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        d = ctx.bar.timestamp.date()
        if d == self._last_day:
            return None
        self._last_day = d
        dc = self._daily_closes(ctx)
        if len(dc) < self.min_days:
            return None
        if ctx.has_position():
            return None
        rets = [math.log(dc[i] / dc[i-1]) for i in range(1, len(dc))]
        recent = rets[-self.look_days:]
        m = sum(recent) / len(recent)
        if len(rets) < 20:
            return None
        sm = sum(rets) / len(rets)
        sig = math.sqrt(sum((r - sm) ** 2 for r in rets) / len(rets))
        if sig <= 1e-10:
            return None
        z = m * self.look_days / (sig * math.sqrt(self.look_days))
        if z > self.z_thresh:
            direction = "long"
        elif z < -self.z_thresh and self.side == "both":
            direction = "short"
        else:
            return None
        conf = min(0.85, 0.55 + (abs(z) - self.z_thresh) * 0.12)
        return ctx.signal(direction, conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": "tsmom", "z": round(z, 3)})
