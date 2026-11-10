from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcLagCatchup(Strategy):
    METADATA = {
        "name": "SolBtcLagCatchup",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_bar = -1
        self.last_exit_bar = -999
        self.cooldown_bars = 16
        self.max_hold_bars = 5

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 25:
            return None

        # Manage existing position time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_reached", "bars_held": bars_held, "price": ctx.bar.close}
                )
            return None

        # Mandatory cooldown after exits
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid crisis/meltdown where cross-asset beta breaks down
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        
        # Calculate SOL short-term return over 3 bars (~15m lag window)
        sol_ret_3bar = (closes[-1] - closes[-4]) / closes[-4] * 100.0

        # Dislocation threshold: BTC momentum strong (>1.2% 24h momentum spike), SOL under-reacting
        # A 1.2% threshold avoids overtrading (~40-80 trades over 17k bars)
        is_long = (btc_ret >= 1.2) and (sol_ret_3bar < 0.35)
        is_short = (btc_ret <= -1.2) and (sol_ret_3bar > -0.35)

        if not (is_long or is_short):
            return None

        direction = "long" if is_long else "short"
        confidence = min(0.9, 0.5 + abs(btc_ret) * 0.1)

        self.entry_bar = ctx.bar_index
        return ctx.signal(
            direction,
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "btc_lead_sol_lag_catchup",
                "btc_return_pct": btc_ret,
                "sol_ret_3bar": sol_ret_3bar,
                "price": ctx.bar.close
            }
        )