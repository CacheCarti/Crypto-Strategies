from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class TripleConfluence(Strategy):
    """v5 winner + a third leg: spread extreme, taker agreeing AND open
    interest expanding over the lookback - size, aggression AND fresh
    leverage all point the same way.
    """
    METADATA = {"name": "v6_btc_confl3_50", "domain": "btc_usdc", "declared_sl_bps": 800, "declared_tp_bps": 1200, "declared_hold_seconds": 86400, "warmup_bars": 576}

    def initialize(self, ctx: BarContext) -> None:
        self.k_tt = "toptrader_pos_ls_btcusdt"
        self.k_ac = "accounts_ls_btcusdt"
        self.k_tk = "taker_ls_btcusdt"
        self.k_oi = "oi_usd_btcusdt"
        self.pct_hi = 0.9
        self.pct_lo = 1.0 - 0.9
        self.taker_thr = 1.1
        self.oi_bps = 50           # min OI expansion over lookback
        self.oi_lb = 288
        self.min_s = 1440
        self.cooldown = 96
        self._spr = deque(maxlen=2880)
        self._oi = deque(maxlen=self.oi_lb + 8)
        self._cd = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        tt = ctx.features.get(self.k_tt)
        ac = ctx.features.get(self.k_ac)
        tk = ctx.features.get(self.k_tk)
        oi = ctx.features.get(self.k_oi)
        if tt is None or ac is None or tk is None or oi is None:
            return None
        spr = float(tt) - float(ac)
        tk = float(tk)
        self._oi.append(float(oi))
        if not self._spr or spr != self._spr[-1]:
            self._spr.append(spr)
        if self._cd > 0:
            self._cd -= 1
        if (ctx.has_position() or len(self._spr) < self.min_s
                or len(self._oi) <= self.oi_lb or self._cd):
            return None
        below = sum(1 for s in self._spr if s < spr)
        pct = below / len(self._spr)
        base = self._oi[0]
        doi = (self._oi[-1] / base - 1.0) * 1e4 if base else 0.0
        sig = None
        if (pct >= self.pct_hi and tk > self.taker_thr
                and doi > self.oi_bps):
            sig = ("long", "triple_long")
        elif (pct <= self.pct_lo and tk < 2.0 - self.taker_thr
                and doi > self.oi_bps):
            sig = ("short", "triple_short")
        if sig is None:
            return None
        direction, reason = sig
        self._cd = self.cooldown
        conf = min(0.85, 0.6 + abs(pct - 0.5) + abs(tk - 1.0) * 0.1)
        return ctx.signal(direction, conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": reason, "pct": round(pct, 3),
                       "taker": round(tk, 3), "doi": round(doi, 1)})
