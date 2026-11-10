from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcLeadSolCatchup(Strategy):
    METADATA = {
        "name": "BtcLeadSolCatchup",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1500,  # ~5 bars of 5m
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.max_hold_bars = 5
        self.entry_bar = -100
        self.last_exit_bar = -100
        self.btc_thresh = 1.35  # Fresh 24h change jump threshold
        self.sol_lag_max = 0.45 # SOL 3-bar return magnitude must be muted

    def _sol_recent_return(self, closes: list, lookback: int = 3) -> float:
        if len(closes) < lookback + 1:
            return 0.0
        return ((closes[-1] - closes[-1 - lookback]) / closes[-1 - lookback]) * 100.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(25)
        if len(closes) < 20:
            return None

        # Check in-position management (time-based exit)
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_horizon_reached", "bars_held": bars_held, "close": ctx.bar.close}
                )
            return None

        # Cooldown guard: strictly limit trade count and avoid re-entries into exhausted moves
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid extreme crisis / meltdown states
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # BTC cross-asset impulse
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        sol_ret_3 = self._sol_recent_return(closes, 3)

        # Trigger conditions: BTC has a distinct directional surge, but SOL has lagged
        long_event = (btc_ret >= self.btc_thresh) and (-0.3 <= sol_ret_3 <= self.sol_lag_max)
        short_event = (btc_ret <= -self.btc_thresh) and (-self.sol_lag_max <= sol_ret_3 <= 0.3)

        if long_event:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_return_pct": btc_ret,
                    "sol_ret_3": sol_ret_3,
                    "price": ctx.bar.close
                }
            )

        if short_event:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_return_pct": btc_ret,
                    "sol_ret_3": sol_ret_3,
                    "price": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index