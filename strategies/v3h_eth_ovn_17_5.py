from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class EthOvernightDrift(Strategy):
    """ETH overnight drift (JRFM 2026: ETH positive overnight drift survived
    a 2021-25 chronological holdout; 05:00 UTC session boundary). Long at
    entry_hour UTC, flat by exit_hour."""
    METADATA = {"name": "v3h_eth_ovn_17_5", "domain": "eth_usdc", "declared_sl_bps": 300, "declared_tp_bps": 800, "declared_hold_seconds": 86400, "warmup_bars": 864}

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour = 17
        self.exit_hour = 5
        self._fired_day = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        h = ctx.bar.timestamp.hour
        if ctx.has_position():
            if h == self.exit_hour:
                return ctx.signal("flat", 0.7, metadata={"reason": "overnight_exit"})
            return None
        if h != self.entry_hour or ctx.bar.timestamp.date() == self._fired_day:
            return None
        self._fired_day = ctx.bar.timestamp.date()
        return ctx.signal("long", 0.6,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={"reason": "eth_overnight"})
