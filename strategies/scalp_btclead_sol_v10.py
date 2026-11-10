from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetBetaCatchup(Strategy):
    METADATA = {
        "name": "SolCrossAssetBetaCatchup",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.max_hold_bars = 5
        self.btc_window = 36
        self.btc_history = []

    def _stddev(self, values):
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 30:
            return None

        current_btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_history.append(current_btc_ret)
        if len(self.btc_history) > self.btc_window:
            self.btc_history.pop(0)

        # Handle active position exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_horizon_exit", "bars_held": bars_held}
                )
            return None

        # Mandatory cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        if len(self.btc_history) < 20:
            return None

        # Dynamic cross-asset move assessment
        btc_mean = sum(self.btc_history) / len(self.btc_history)
        btc_std = self._stddev(self.btc_history)
        if btc_std < 0.15:
            return None

        # Freshness: measure delta over last 3 bars
        btc_3bar_ago = self.btc_history[-4] if len(self.btc_history) >= 4 else self.btc_history[0]
        btc_delta = current_btc_ret - btc_3bar_ago

        # SOL local return over last 3 bars (bps)
        sol_return_3b = (closes[-1] - closes[-4]) / closes[-4] * 100.0

        # BTC z-score
        btc_z = (current_btc_ret - btc_mean) / btc_std

        # Thresholds: sharp move dynamically (> 2.0 std dev) AND delta is fresh (> 1.2%)
        # SOL must be lagging behind relative to expected beta
        if btc_z > 2.0 and btc_delta > 1.2 and sol_return_3b < 0.4:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.5 + abs(btc_z) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=120.0,
                take_profit_bps=220.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_beta_lag_long",
                    "btc_z": round(btc_z, 2),
                    "btc_delta": round(btc_delta, 2),
                    "sol_return_3b": round(sol_return_3b, 3),
                    "price": closes[-1]
                }
            )

        if btc_z < -2.0 and btc_delta < -1.2 and sol_return_3b > -0.4:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.5 + abs(btc_z) * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=120.0,
                take_profit_bps=220.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_beta_lag_short",
                    "btc_z": round(btc_z, 2),
                    "btc_delta": round(btc_delta, 2),
                    "sol_return_3b": round(sol_return_3b, 3),
                    "price": closes[-1]
                }
            )

        return None