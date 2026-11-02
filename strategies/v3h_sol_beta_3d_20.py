from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class SolBetaSwitch(Strategy):
    """SOL = leveraged BTC beta (Blockworks: SOL beats BTC in 4/5 bull legs,
    median 3.54x; gives back more in selloffs). Long SOL only while the BTC
    trailing trend (ctx.features btc_return_Nd_pct) is positive."""
    METADATA = {"name": "v3h_sol_beta_3d_20", "domain": "sol_usdc", "declared_sl_bps": 800, "declared_tp_bps": 1200, "declared_hold_seconds": 259200, "warmup_bars": 2316}

    def initialize(self, ctx: BarContext) -> None:
        self.fkey = "btc_return_3d_pct"              # btc_return_1d_pct / 3d / 7d
        self.thresh = 2.0            # min BTC pct to engage
        self.cooldown_bars = 288
        self._last_day = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        d = ctx.bar.timestamp.date()
        if d == self._last_day:
            return None
        self._last_day = d
        if ctx.has_position():
            btc = ctx.features.get(self.fkey)
            if btc is not None and float(btc) < 0:
                return ctx.signal("flat", 0.7, metadata={"reason": "btc_trend_off"})
            return None
        btc = ctx.features.get(self.fkey)
        if btc is None or float(btc) < self.thresh:
            return None
        return ctx.signal("long", 0.62,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": "sol_beta_switch", "btc_ret": float(btc)})
