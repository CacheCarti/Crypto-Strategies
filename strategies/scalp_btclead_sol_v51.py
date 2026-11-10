from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetLeadLagScalp(Strategy):
    METADATA = {
        "name": "SolCrossAssetLeadLagScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,   # Scalp ~4 5-min bars
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 4

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        # 1. Manage Active Position (Time-based or target exit)
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_decay_exit", "bars_held": bars_held, "close": ctx.bar.close}
                )
            return None

        # 2. Enforce Mandatory Cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # 3. Regime & Volatility Check (Avoid extreme meltdown conditions)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # 4. Feature: BTC 24h Return
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # 5. Local SOL Momentum (5m Return over 3 bars)
        sol_ret_3 = (closes[-1] - closes[-4]) / closes[-4] * 100.0

        # Event Boundary: BTC shows strong sustained momentum (|btc_ret| > 1.8%)
        # but SOL has lagged / consolidated over the past 3 bars (|sol_ret_3| < 0.35%)
        # This provides a crisp dislocation window without indicator churn.
        long_dislocation = (btc_ret >= 1.8) and (-0.35 <= sol_ret_3 <= 0.30)
        short_dislocation = (btc_ret <= -1.8) and (-0.30 <= sol_ret_3 <= 0.35)

        if long_dislocation:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.55 + (abs(btc_ret) - 1.8) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=75.0,
                take_profit_bps=160.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_return_pct": btc_ret,
                    "sol_ret_3": sol_ret_3,
                    "price": ctx.bar.close,
                }
            )

        if short_dislocation:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.55 + (abs(btc_ret) - 1.8) * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=75.0,
                take_profit_bps=160.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_return_pct": btc_ret,
                    "sol_ret_3": sol_ret_3,
                    "price": ctx.bar.close,
                }
            )

        return None