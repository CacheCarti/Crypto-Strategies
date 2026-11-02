from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class FundingAsym(Strategy):
    """Funding-percentile contrarian/momentum.

    Tracks the per-8h funding rate series from ctx.features, computes the
    percentile rank of the latest print over the trailing window, and trades
    the documented asymmetry: top-decile prints resolve DOWN on BTC but UP on
    ETH/SOL (MarketTrace 789d cross-venue study); bottom-decile prints are a
    coin flip everywhere except the AIJMR BTC +50bps/24h finding - grid both.
    """
    METADATA = {"name": "v3h_eth_fund_60_90_fadehi_72", "domain": "eth_usdc", "declared_sl_bps": 800, "declared_tp_bps": 800, "declared_hold_seconds": 259200, "warmup_bars": 288}

    def initialize(self, ctx: BarContext) -> None:
        self.fkey = "funding_rate_ethusdt"              # e.g. funding_rate_btcusdt
        self.lookback_prints = 180       # 8h prints in percentile window
        self.pct_hi = 0.9            # e.g. 0.90
        self.pct_lo = 1.0 - self.pct_hi
        self.mode = "fade"              # fade | ride | both
        self.side = "hi"              # hi|lo|both - which tail to trade
        self.min_prints = max(30, self.lookback_prints // 3)
        self._rates = deque(maxlen=self.lookback_prints)
        self._last_rate = None
        self.cooldown_bars = 96           # 8h between signals

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        fr = ctx.features.get(self.fkey)
        if fr is None:
            return None
        fr = float(fr)
        if self._last_rate is None or fr != self._last_rate:
            self._rates.append(fr)
            self._last_rate = fr
        if len(self._rates) < self.min_prints:
            return None
        if ctx.has_position():
            return None
        below = sum(1 for r in self._rates if r < fr)
        pct = below / len(self._rates)

        sig = None
        if pct >= self.pct_hi and self.side in ("hi", "both"):
            # top-decile print: crowded longs
            if self.mode == "fade":
                sig = ("short", "top_decile_fade")
            elif self.mode == "ride":
                sig = ("long", "top_decile_ride")
            else:  # both - asset decides via param chosen at gen time
                sig = ("", "top_decile")
        elif pct <= self.pct_lo and self.side in ("lo", "both"):
            if self.mode == "fade":
                sig = ("long", "bottom_decile_fade")   # shorts crowded -> squeeze up
            elif self.mode == "ride":
                sig = ("short", "bottom_decile_ride")
            else:
                sig = ("", "bottom_decile")
        if sig is None:
            return None
        direction, reason = sig
        conf = min(0.85, 0.55 + abs(pct - 0.5) * 0.8)
        return ctx.signal(direction, conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": reason, "funding_pct": round(pct, 3),
                       "funding_rate": fr})
