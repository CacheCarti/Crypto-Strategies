from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class SpreadTakerConfluence(Strategy):
    """Whales-vs-crowd spread extreme AND taker flow tilted the same way.
    v5's only portfolio winner (sol 90/1.1/24h). Neighborhood grid +
    ETH/BTC port + optional spread persistence.
    """
    METADATA = {"name": "v6_sol_confl_90_110_h12", "domain": "sol_usdc", "declared_sl_bps": 600, "declared_tp_bps": 900, "declared_hold_seconds": 43200, "warmup_bars": 288}

    def initialize(self, ctx: BarContext) -> None:
        self.k_tt = "toptrader_pos_ls_solusdt"
        self.k_ac = "accounts_ls_solusdt"
        self.k_tk = "taker_ls_solusdt"
        self.pct_hi = 0.9
        self.pct_lo = 1.0 - 0.9
        self.taker_thr = 1.1
        self.persist = 1      # 1 = single print suffices
        self.min_s = 1440
        self.cooldown = 96
        self._spr = deque(maxlen=2880)
        self._streak_hi = 0
        self._streak_lo = 0
        self._cd = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        tt = ctx.features.get(self.k_tt)
        ac = ctx.features.get(self.k_ac)
        tk = ctx.features.get(self.k_tk)
        if tt is None or ac is None or tk is None:
            return None
        spr = float(tt) - float(ac)
        tk = float(tk)
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
        if (pct >= self.pct_hi and self._streak_hi >= self.persist
                and tk > self.taker_thr):
            sig = ("long", "whales_flow_long")
        elif (pct <= self.pct_lo and self._streak_lo >= self.persist
                and tk < 2.0 - self.taker_thr):
            sig = ("short", "whales_flow_short")
        if sig is None:
            return None
        direction, reason = sig
        self._cd = self.cooldown
        self._streak_hi = self._streak_lo = 0
        conf = min(0.8, 0.55 + abs(pct - 0.5) + abs(tk - 1.0) * 0.1)
        return ctx.signal(direction, conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": reason, "pct": round(pct, 3),
                       "spread": round(spr, 4), "taker": round(tk, 3)})
