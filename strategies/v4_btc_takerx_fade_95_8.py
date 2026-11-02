from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class TakerExtreme(Strategy):
    """Taker buy/sell volume ratio extremes, percentile-ranked.

    taker_ls > pct_hi means aggressive market buying is historically extreme:
    mode=fade treats it as exhaustion -> short; mode=ride treats the flow as
    leadership -> long. Bottom tail is the mirror image.
    """
    METADATA = {"name": "v4_btc_takerx_fade_95_8", "domain": "btc_usdc", "declared_sl_bps": 500, "declared_tp_bps": 750, "declared_hold_seconds": 28800, "warmup_bars": 288}

    def initialize(self, ctx: BarContext) -> None:
        self.tkey = "taker_ls_btcusdt"
        self.pct_hi = 0.95
        self.pct_lo = 1.0 - 0.95
        self.mode = "fade"          # fade | ride
        self.min_s = 1440
        self.cooldown = 48
        self._vals = deque(maxlen=2880)
        self._cd = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        v = ctx.features.get(self.tkey)
        if v is None:
            return None
        v = float(v)
        if not self._vals or v != self._vals[-1]:
            self._vals.append(v)
        if self._cd > 0:
            self._cd -= 1
        if ctx.has_position() or len(self._vals) < self.min_s or self._cd:
            return None
        below = sum(1 for x in self._vals if x < v)
        pct = below / len(self._vals)
        sig = None
        if pct >= self.pct_hi:
            sig = ("short", "buy_exhaustion") if self.mode == "fade" \
                  else ("long", "flow_leads")
        elif pct <= self.pct_lo:
            sig = ("long", "sell_exhaustion") if self.mode == "fade" \
                  else ("short", "flow_leads_dn")
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
                       "taker": round(v, 4)})
