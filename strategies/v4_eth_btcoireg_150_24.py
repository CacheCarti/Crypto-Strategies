from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class BTCOIRegime(Strategy):
    """Cross-domain filter: only take momentum on this alt while BTC open
    interest is EXPANDING and BTC 1d return is positive - leverage is
    flowing into the market, so alt momentum has fuel behind it. When BTC
    OI contracts, the same momentum is running on fumes: stay out.
    """
    METADATA = {"name": "v4_eth_btcoireg_150_24", "domain": "eth_usdc", "declared_sl_bps": 800, "declared_tp_bps": 1200, "declared_hold_seconds": 86400, "warmup_bars": 448}

    def initialize(self, ctx: BarContext) -> None:
        self.btc_oi_key = "oi_usd_btcusdt"
        self.btc_mom_key = "btc_return_1d_pct"
        self.lookback = 144           # 12h OI window
        self.oi_bps = 100        # min BTC OI expansion
        self.mom_bps = 150      # min alt 12h momentum
        self.cooldown = 144
        self._oi = deque(maxlen=self.lookback + 8)
        self._cd = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        boi = ctx.features.get(self.btc_oi_key)
        if boi is not None:
            self._oi.append(float(boi))
        if self._cd > 0:
            self._cd -= 1
        if ctx.has_position() or len(self._oi) <= self.lookback or self._cd:
            return None
        base = self._oi[0]
        if not base:
            return None
        btc_oi_chg = (self._oi[-1] / base - 1.0) * 1e4
        if btc_oi_chg < self.oi_bps:
            return None
        bmom = ctx.features.get(self.btc_mom_key)
        if bmom is None or float(bmom) <= 0:
            return None
        closes = ctx.closes(self.lookback + 1)
        if len(closes) <= self.lookback:
            return None
        mom = (closes[-1] / closes[0] - 1.0) * 1e4
        if mom < self.mom_bps:
            return None
        self._cd = self.cooldown
        conf = min(0.8, 0.55 + (mom + btc_oi_chg) / 20000.0)
        return ctx.signal("long", conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": "btc_oi_regime", "mom": round(mom, 1),
                       "boi": round(btc_oi_chg, 1)})
