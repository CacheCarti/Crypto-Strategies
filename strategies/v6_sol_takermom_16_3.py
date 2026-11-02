from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class TakerMomentum(Strategy):
    """Sustained taker imbalance over a short window: taker_ls holding
    away from 1.0 for `persist` prints means one side is aggressing
    continuously - ride with the flow, not against it.
    """
    METADATA = {"name": "v6_sol_takermom_16_3", "domain": "sol_usdc", "declared_sl_bps": 400, "declared_tp_bps": 600, "declared_hold_seconds": 14400, "warmup_bars": 288}

    def initialize(self, ctx: BarContext) -> None:
        self.tkey = "taker_ls_solusdt"
        self.thr = 1.6              # |taker_ls - 1| threshold
        self.persist = 3      # consecutive prints required
        self.cooldown = 48
        self._streak_hi = 0
        self._streak_lo = 0
        self._cd = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        v = ctx.features.get(self.tkey)
        if v is None:
            return None
        v = float(v)
        if self._cd > 0:
            self._cd -= 1
        if ctx.has_position() or self._cd:
            return None
        self._streak_hi = self._streak_hi + 1 if v > self.thr else 0
        self._streak_lo = self._streak_lo + 1 if v < 2.0 - self.thr else 0
        sig = None
        if self._streak_hi >= self.persist:
            sig = ("long", "taker_buy_flow")
        elif self._streak_lo >= self.persist:
            sig = ("short", "taker_sell_flow")
        if sig is None:
            return None
        direction, reason = sig
        self._cd = self.cooldown
        self._streak_hi = self._streak_lo = 0
        conf = min(0.8, 0.55 + abs(v - 1.0) * 0.2)
        return ctx.signal(direction, conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": reason, "taker": round(v, 3)})
