from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class PositioningSpreadPersist(Strategy):
    """v4 possprd iterate: whales-vs-crowd spread extreme that must PERSIST.

    v4 found the raw extreme fired too often and decayed mid-window. This
    requires the spread percentile to stay above/below the threshold for
    `persist` consecutive prints before entering, and (if trend_gate) only
    rides longs when the asset's own 1d momentum agrees.
    """
    METADATA = {"name": "v5_eth_possprd2_92_p3_tg", "domain": "eth_usdc", "declared_sl_bps": 800, "declared_tp_bps": 1200, "declared_hold_seconds": 86400, "warmup_bars": 288}

    def initialize(self, ctx: BarContext) -> None:
        self.k_tt = "toptrader_pos_ls_ethusdt"
        self.k_ac = "accounts_ls_ethusdt"
        self.rkey = "eth_return_1d_pct"
        self.pct_hi = 0.92
        self.pct_lo = 1.0 - 0.92
        self.persist = 3      # consecutive extreme prints required
        self.trend_gate = 1        # 1 = momentum must agree with direction
        self.min_s = 1440
        self.cooldown = 96
        self._spr = deque(maxlen=2880)
        self._streak_hi = 0
        self._streak_lo = 0
        self._cd = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        tt = ctx.features.get(self.k_tt)
        ac = ctx.features.get(self.k_ac)
        if tt is None or ac is None:
            return None
        spr = float(tt) - float(ac)
        if not self._spr or spr != self._spr[-1]:
            self._spr.append(spr)
        if self._cd > 0:
            self._cd -= 1
        if ctx.has_position() or len(self._spr) < self.min_s or self._cd:
            return None
        below = sum(1 for s in self._spr if s < spr)
        pct = below / len(self._spr)
        self._streak_hi = self._streak_hi + 1 if pct >= self.pct_hi else 0
        self._streak_lo = self._streak_lo + 1 if pct <= self.pct_lo else 0
        sig = None
        mom_ok_l = mom_ok_s = True
        if self.trend_gate:
            r = ctx.features.get(self.rkey)
            if r is not None:
                r = float(r)
                mom_ok_l = r > 0
                mom_ok_s = r < 0
        if self._streak_hi >= self.persist and mom_ok_l:
            sig = ("long", "whales_lead_persist")
        elif self._streak_lo >= self.persist and mom_ok_s:
            sig = ("short", "whales_short_persist")
        if sig is None:
            return None
        direction, reason = sig
        self._cd = self.cooldown
        self._streak_hi = self._streak_lo = 0
        conf = min(0.8, 0.55 + abs(pct - 0.5))
        return ctx.signal(direction, conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": reason, "pct": round(pct, 3),
                       "spread": round(spr, 4)})
