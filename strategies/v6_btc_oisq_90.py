from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class FundingOISqueeze(Strategy):
    """Funding extreme while OI contracts = the crowded side is paying to
    hold and still shrinking - the unwind has fuel. Ride AGAINST the
    crowded side: crowded longs unwinding push price down (short);
    crowded shorts unwinding push price up (long).
    """
    METADATA = {"name": "v6_btc_oisq_90", "domain": "btc_usdc", "declared_sl_bps": 800, "declared_tp_bps": 1200, "declared_hold_seconds": 86400, "warmup_bars": 576}

    def initialize(self, ctx: BarContext) -> None:
        self.f_key = "funding_rate_btcusdt"
        self.oi_key = "oi_usd_btcusdt"
        self.f_pct = 0.9
        self.oi_bps = 100
        self.oi_lb = 288
        self.min_f = 30
        self.cooldown = 288
        self._fr = deque(maxlen=90)
        self._last_fr = None
        self._oi = deque(maxlen=self.oi_lb + 8)
        self._cd = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        fr = ctx.features.get(self.f_key)
        if fr is not None:
            fr = float(fr)
            if self._last_fr is None or fr != self._last_fr:
                self._fr.append(fr)
                self._last_fr = fr
        oi = ctx.features.get(self.oi_key)
        if oi is not None:
            self._oi.append(float(oi))
        if self._cd > 0:
            self._cd -= 1
        if (ctx.has_position() or len(self._fr) < self.min_f
                or len(self._oi) <= self.oi_lb or self._cd):
            return None
        below = sum(1 for r in self._fr if r < self._fr[-1])
        fpct = below / len(self._fr)
        base = self._oi[0]
        if not base:
            return None
        doi = (self._oi[-1] / base - 1.0) * 1e4
        if doi > -self.oi_bps:
            return None            # OI must be contracting
        sig = None
        if fpct >= self.f_pct:
            sig = ("short", "longs_unwind")
        elif fpct <= 1.0 - self.f_pct:
            sig = ("long", "shorts_unwind")
        if sig is None:
            return None
        direction, reason = sig
        self._cd = self.cooldown
        conf = min(0.8, 0.55 + abs(fpct - 0.5) + abs(doi) / 40000.0)
        return ctx.signal(direction, conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": reason, "fpct": round(fpct, 3),
                       "doi": round(doi, 1)})
