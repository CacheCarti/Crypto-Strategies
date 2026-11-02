from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class OISlopeMomentum(Strategy):
    """Own-asset momentum, but only while OI itself is expanding - moves
    backed by fresh leverage carry further than moves on a shrinking book.
    mode=contract tests the opposite regime for completeness.
    """
    METADATA = {"name": "v6_btc_oislope_con", "domain": "btc_usdc", "declared_sl_bps": 600, "declared_tp_bps": 900, "declared_hold_seconds": 43200, "warmup_bars": 448}

    def initialize(self, ctx: BarContext) -> None:
        self.oi_key = "oi_usd_btcusdt"
        self.lookback = 144          # OI-change window
        self.mom_lb = 144           # price-momentum window
        self.oi_bps = 100
        self.mom_bps = 100
        self.mode = "contract"          # expand | contract
        self.cooldown = 144
        self._oi = deque(maxlen=self.lookback + 8)
        self._cd = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        oi = ctx.features.get(self.oi_key)
        if oi is None:
            return None
        self._oi.append(float(oi))
        if self._cd > 0:
            self._cd -= 1
        if ctx.has_position() or len(self._oi) <= self.lookback or self._cd:
            return None
        closes = ctx.closes(self.mom_lb + 1)
        if len(closes) <= self.mom_lb:
            return None
        base = self._oi[0]
        if not base:
            return None
        doi = (self._oi[-1] / base - 1.0) * 1e4
        mom = (closes[-1] / closes[0] - 1.0) * 1e4
        oi_ok = doi > self.oi_bps if self.mode == "expand" \
                else doi < -self.oi_bps
        sig = None
        if oi_ok and mom > self.mom_bps:
            sig = ("long", "oi_" + self.mode + "_mom_up")
        elif oi_ok and mom < -self.mom_bps:
            sig = ("short", "oi_" + self.mode + "_mom_dn")
        if sig is None:
            return None
        direction, reason = sig
        self._cd = self.cooldown
        conf = min(0.8, 0.55 + (abs(mom) + abs(doi)) / 20000.0)
        return ctx.signal(direction, conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": reason, "mom": round(mom, 1),
                       "doi": round(doi, 1)})
