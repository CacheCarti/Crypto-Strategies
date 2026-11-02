from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class TopTraderDivergence(Strategy):
    """Inside the top-trader cohort: position-weighted long/short vs
    count-weighted long/short disagreeing. When the SIZE-weighted read
    diverges from the COUNT read, the biggest accounts sit on the
    size side - ride that direction.
    """
    METADATA = {"name": "v6_btc_ttdiv_90", "domain": "btc_usdc", "declared_sl_bps": 800, "declared_tp_bps": 1200, "declared_hold_seconds": 86400, "warmup_bars": 288}

    def initialize(self, ctx: BarContext) -> None:
        self.k_pos = "toptrader_pos_ls_btcusdt"
        self.k_cnt = "toptrader_ls_btcusdt"
        self.pct_hi = 0.9
        self.pct_lo = 1.0 - 0.9
        self.min_s = 1440
        self.cooldown = 96
        self._spr = deque(maxlen=2880)
        self._cd = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        p = ctx.features.get(self.k_pos)
        c = ctx.features.get(self.k_cnt)
        if p is None or c is None:
            return None
        spr = float(p) - float(c)
        if not self._spr or spr != self._spr[-1]:
            self._spr.append(spr)
        if self._cd > 0:
            self._cd -= 1
        if ctx.has_position() or len(self._spr) < self.min_s or self._cd:
            return None
        below = sum(1 for s in self._spr if s < spr)
        pct = below / len(self._spr)
        sig = None
        if pct >= self.pct_hi:
            sig = ("long", "size_leads_long")
        elif pct <= self.pct_lo:
            sig = ("short", "size_leads_short")
        if sig is None:
            return None
        direction, reason = sig
        self._cd = self.cooldown
        conf = min(0.8, 0.55 + abs(pct - 0.5))
        return ctx.signal(direction, conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": reason, "pct": round(pct, 3),
                       "spread": round(spr, 4)})
