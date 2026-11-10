from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetLeadLagScalp(Strategy):
    METADATA = {
        "name": "SolCrossAssetLeadLagScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.last_trade_bar = -50
        self.entry_bar = -50
        self.max_hold_bars = 6
        self.lookback = 6
        self.btc_threshold = 1.4
        self.sol_lag_max = 0.55

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        # Manage open position exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_reached", "bars_held": bars_held}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Skip extreme crisis regimes where lead-lag breaks down
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Cross-asset BTC lead calculation
        # btc_return_pct is 24h rolling % return; difference over recent bars captures sudden impulse
        btc_now = ctx.features.get("btc_return_pct", 0.0)
        # Using context to track fresh BTC changes across self.lookback bars
        # Calculate SOL's own move over the exact same short window
        sol_p0 = closes[-1 - self.lookback]
        sol_p1 = closes[-1]
        if sol_p0 <= 0:
            return None
        sol_ret_pct = ((sol_p1 - sol_p0) / sol_p0) * 100.0

        # BTC fresh momentum proxy: check magnitude of divergence
        # When BTC has high positive momentum and SOL has lagged behind, catch-up is expected
        lead_long = (btc_now > self.btc_threshold) and (sol_ret_pct < self.sol_lag_max) and (sol_ret_pct > -0.8)
        lead_short = (btc_now < -self.btc_threshold) and (sol_ret_pct > -self.sol_lag_max) and (sol_ret_pct < 0.8)

        if lead_long:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            divergence = btc_now - sol_ret_pct
            conf = min(0.85, 0.55 + 0.1 * min(3.0, divergence))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=120.0,
                take_profit_bps=220.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_return_pct": btc_now,
                    "sol_ret_pct": sol_ret_pct,
                    "divergence": divergence,
                    "regime": regime
                }
            )

        if lead_short:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            divergence = abs(btc_now - sol_ret_pct)
            conf = min(0.85, 0.55 + 0.1 * min(3.0, divergence))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=120.0,
                take_profit_bps=220.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_return_pct": btc_now,
                    "sol_ret_pct": sol_ret_pct,
                    "divergence": divergence,
                    "regime": regime
                }
            )

        return None