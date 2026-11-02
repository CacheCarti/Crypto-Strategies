from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class OIDivergence(Strategy):
    """OI/price divergence on 5m bars.

    Over the lookback window compares the price move with the open-interest
    move. mode=fade: price up + OI down is a short-covering rally (no new
    conviction) -> fade; price down + OI down is a liquidation flush ->
    bounce long. mode=conf: price + OI rising together means NEW positions
    entering in the move direction -> ride it.
    """
    METADATA = {"name": "v4_eth_oidiv_144_250_fade", "domain": "eth_usdc", "declared_sl_bps": 500, "declared_tp_bps": 700, "declared_hold_seconds": 28800, "warmup_bars": 160}

    def initialize(self, ctx: BarContext) -> None:
        self.oi_key = "oi_usd_ethusdt"
        self.lookback = 144          # bars in divergence window
        self.px_bps = 150        # min |price move| to qualify
        self.oi_bps = 250        # min |OI move| to qualify
        self.mode = "fade"          # fade | conf
        self.cooldown = 144          # bars between signals
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
        sig = None
        if self.mode == "fade":
            if dpx > self.px_bps and doi < -self.oi_bps:
                sig = ("short", "covering_fade")
            elif dpx < -self.px_bps and doi < -self.oi_bps:
                sig = ("long", "capitulation_bounce")
        else:
            if dpx > self.px_bps and doi > self.oi_bps:
                sig = ("long", "new_longs_confirm")
            elif dpx < -self.px_bps and doi > self.oi_bps:
                sig = ("short", "new_shorts_confirm")
        if sig is None:
            return None
        direction, reason = sig
        self._cd = self.cooldown
        conf = min(0.8, 0.55 + (abs(dpx) + abs(doi)) / 20000.0)
        return ctx.signal(direction, conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": reason, "dpx": round(dpx, 1),
                       "doi": round(doi, 1)})
