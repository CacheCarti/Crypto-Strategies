from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class ConflDirectional(Strategy):
    """Whales-vs-crowd spread extreme + taker flow agreeing, direction
    restricted to long. v6b: the autopsy showed SOL confl longs
    earn (+$12.9/50 trades) while shorts bleed (-$40.5/53) - crowded
    shorts are squeeze fuel on SOL, not confirmation of downside.
    """
    METADATA = {"name": "v6b_btc_conflL_90_110", "domain": "btc_usdc", "declared_sl_bps": 800, "declared_tp_bps": 1200, "declared_hold_seconds": 86400, "warmup_bars": 288}

    def initialize(self, ctx: BarContext) -> None:
        self.k_tt = "toptrader_pos_ls_btcusdt"
        self.k_ac = "accounts_ls_btcusdt"
        self.k_tk = "taker_ls_btcusdt"
        self.pct_hi = 0.9
        self.pct_lo = 1.0 - 0.9
        self.taker_thr = 1.1
        self.dir_mode = "long"   # long | short | both
        self.min_s = 1440
        self.cooldown = 96
        self._spr = deque(maxlen=2880)
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
        sig = None
        if (self.dir_mode in ("long", "both") and pct >= self.pct_hi
                and tk > self.taker_thr):
            sig = ("long", "whales_flow_long")
        elif (self.dir_mode in ("short", "both") and pct <= self.pct_lo
                and tk < 2.0 - self.taker_thr):
            sig = ("short", "whales_flow_short")
        if sig is None:
            return None
        direction, reason = sig
        self._cd = self.cooldown
        conf = min(0.8, 0.55 + abs(pct - 0.5) + abs(tk - 1.0) * 0.1)
        return ctx.signal(direction, conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": reason, "pct": round(pct, 3),
                       "spread": round(spr, 4), "taker": round(tk, 3)})
