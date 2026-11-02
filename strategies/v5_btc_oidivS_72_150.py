from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class OIDivShortOnly(Strategy):
    """v4 autopsy: on SOL the OI-confirmation shorts made +87bps/trade while
    longs lost -12bps. This trades ONLY the downside confirmation leg -
    price falling while OI expands = new shorts piling in, ride the flush.
    """
    METADATA = {"name": "v5_btc_oidivS_72_150", "domain": "btc_usdc", "declared_sl_bps": 500, "declared_tp_bps": 750, "declared_hold_seconds": 28800, "warmup_bars": 88}

    def initialize(self, ctx: BarContext) -> None:
        self.oi_key = "oi_usd_btcusdt"
        self.lookback = 72
        self.px_bps = 100
        self.oi_bps = 150
        self.cooldown = 72
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
        dpx = (closes[-1] / closes[0] - 1.0) * 1e4
        doi = (self._oi[-1] / base - 1.0) * 1e4
        if not (dpx < -self.px_bps and doi > self.oi_bps):
            return None
        self._cd = self.cooldown
        conf = min(0.8, 0.55 + (abs(dpx) + abs(doi)) / 20000.0)
        return ctx.signal("short", conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": "oi_confirm_short", "dpx": round(dpx, 1),
                       "doi": round(doi, 1)})
