from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class OIWipeout(Strategy):
    """Capitulation marker: a large open-interest drop means leveraged
    positions were flushed out - the crowd that was leaning is gone.
    Optional funding-percentile gate requires the flush to have happened
    while carry was extreme (crowded side getting rekt). Long-only.
    """
    METADATA = {"name": "v4_btc_capit_200_fg", "domain": "btc_usdc", "declared_sl_bps": 800, "declared_tp_bps": 1200, "declared_hold_seconds": 86400, "warmup_bars": 576}

    def initialize(self, ctx: BarContext) -> None:
        self.oi_key = "oi_usd_btcusdt"
        self.f_key = "funding_rate_btcusdt"
        self.lookback = 288          # bars for the OI-change window
        self.wipe_bps = 200          # OI drop threshold, bps
        self.fund_gate = 1         # 1 require extreme funding, 0 off
        self.f_pct = 0.9
        self.min_f = 30
        self.cooldown = 288
        self._oi = deque(maxlen=self.lookback + 8)
        self._fr = deque(maxlen=90)
        self._last_fr = None
        self._cd = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        oi = ctx.features.get(self.oi_key)
        if oi is not None:
            self._oi.append(float(oi))
        fr = ctx.features.get(self.f_key)
        if fr is not None:
            fr = float(fr)
            if self._last_fr is None or fr != self._last_fr:
                self._fr.append(fr)
                self._last_fr = fr
        if self._cd > 0:
            self._cd -= 1
        if ctx.has_position() or len(self._oi) <= self.lookback or self._cd:
            return None
        base = self._oi[0]
        if not base:
            return None
        doi = (self._oi[-1] / base - 1.0) * 1e4
        if doi > -self.wipe_bps:
            return None
        if self.fund_gate:
            if len(self._fr) < self.min_f:
                return None
            below = sum(1 for r in self._fr if r < self._fr[-1])
            fpct = below / len(self._fr)
            if fpct < self.f_pct:
                return None
        self._cd = self.cooldown
        conf = min(0.8, 0.6 + abs(doi) / 20000.0)
        return ctx.signal("long", conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": "oi_wipeout", "doi": round(doi, 1)})
