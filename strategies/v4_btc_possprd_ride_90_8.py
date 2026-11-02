from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class PositioningSpread(Strategy):
    """Top-trader vs crowd positioning spread, percentile-ranked.

    spread = toptrader_pos_ls - accounts_ls. When it sits in the top
    percentile whales are positioned long vs the crowd (mode=ride -> long);
    the bottom percentile is the mirror. mode=fade bets the crowd extreme
    resolves the other way. Prints deduped on value change (5-min cadence).
    """
    METADATA = {"name": "v4_btc_possprd_ride_90_8", "domain": "btc_usdc", "declared_sl_bps": 500, "declared_tp_bps": 750, "declared_hold_seconds": 28800, "warmup_bars": 288}

    def initialize(self, ctx: BarContext) -> None:
        self.k_tt = "toptrader_pos_ls_btcusdt"
        self.k_ac = "accounts_ls_btcusdt"
        self.pct_hi = 0.9
        self.pct_lo = 1.0 - 0.9
        self.mode = "ride"          # ride | fade
        self.min_s = 1440
        self.cooldown = 96
        self._spr = deque(maxlen=2880)
        self._cd = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        tt = ctx.features.get(self.k_tt)
        ac = ctx.features.get(self.k_ac)
        if tt is None or ac is None:
            return None
        spr = float(tt) - float(ac)
        if not self._spr or spr != self._spr[-1]:
            self._spr.append(spr)
        if self._cd > 0:
            self._cd -= 1
        if ctx.has_position() or len(self._spr) < self.min_s or self._cd:
            return None
        below = sum(1 for s in self._spr if s < spr)
        pct = below / len(self._spr)
        sig = None
        if pct >= self.pct_hi:
            sig = ("long", "whales_lead") if self.mode == "ride" \
                  else ("short", "spread_fade_hi")
        elif pct <= self.pct_lo:
            sig = ("short", "whales_short") if self.mode == "ride" \
                  else ("long", "spread_fade_lo")
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
                       "spread": round(spr, 4)})
