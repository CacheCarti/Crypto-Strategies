from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class OIDivTrend(Strategy):
    """v4 oidiv iterate: price+OI confirmation, but the higher-timeframe
    trend must agree. SOL's v4 bleed came from longing OI-confirmed pops
    inside a downtrend - this refuses a long unless the asset's own 1d
    return is positive (mirror for shorts).
    """
    METADATA = {"name": "v5_eth_oidivT_72_250", "domain": "eth_usdc", "declared_sl_bps": 500, "declared_tp_bps": 750, "declared_hold_seconds": 28800, "warmup_bars": 88}

    def initialize(self, ctx: BarContext) -> None:
        self.oi_key = "oi_usd_ethusdt"
        self.rkey = "eth_return_1d_pct"
        self.lookback = 72
        self.px_bps = 100
        self.oi_bps = 250
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
        r = ctx.features.get(self.rkey)
        r = float(r) if r is not None else 0.0
        sig = None
        if dpx > self.px_bps and doi > self.oi_bps and r > 0:
            sig = ("long", "oi_confirm_trend_up")
        elif dpx < -self.px_bps and doi > self.oi_bps and r < 0:
            sig = ("short", "oi_confirm_trend_dn")
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
                       "doi": round(doi, 1), "mom1d": round(r, 2)})
