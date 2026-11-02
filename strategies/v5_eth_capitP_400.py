from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class OIWipeoutConfirmed(Strategy):
    """v4 capit iterate: OI wipeout alone fired on any OI dip. This requires
    the wipeout to accompany an actual price flush - leverage flushed AND
    price dumped = the liquidation event is real, not positioning drift.
    Long-only bounce.
    """
    METADATA = {"name": "v5_eth_capitP_400", "domain": "eth_usdc", "declared_sl_bps": 800, "declared_tp_bps": 1200, "declared_hold_seconds": 86400, "warmup_bars": 576}

    def initialize(self, ctx: BarContext) -> None:
        self.oi_key = "oi_usd_ethusdt"
        self.lookback = 288
        self.wipe_bps = 400
        self.px_bps = 100           # price must also be down this much
        self.cooldown = 288
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
        closes = ctx.closes(self.lookback + 1)
        if len(closes) <= self.lookback:
            return None
        base = self._oi[0]
        if not base:
            return None
        doi = (self._oi[-1] / base - 1.0) * 1e4
        dpx = (closes[-1] / closes[0] - 1.0) * 1e4
        if not (doi < -self.wipe_bps and dpx < -self.px_bps):
            return None
        self._cd = self.cooldown
        conf = min(0.8, 0.6 + (abs(doi) + abs(dpx)) / 25000.0)
        return ctx.signal("long", conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": "oi_wipeout_pxflush", "doi": round(doi, 1),
                       "dpx": round(dpx, 1)})
