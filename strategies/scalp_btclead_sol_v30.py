from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetBetaCatchup(Strategy):
    METADATA = {
        "name": "SolCrossAssetBetaCatchup",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.max_hold_bars = 5
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.last_btc_returns = []

    def _stddev(self, values):
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        var = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.last_btc_returns.append(btc_ret)
        if len(self.last_btc_returns) > 60:
            self.last_btc_returns.pop(0)

        # Handle active position exit / time-stop
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "time_horizon_reached", "bars_held": bars_held})
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if len(self.last_btc_returns) < 30:
            return None

        # Track rolling volatility of BTC returns to dynamically define an outlier move
        btc_std = self._stddev(self.last_btc_returns)
        if btc_std < 0.001:
            return None

        btc_zscore = (btc_ret - (sum(self.last_btc_returns) / len(self.last_btc_returns))) / btc_std
        fresh_btc_move = btc_ret - self.last_btc_returns[-4] if len(self.last_btc_returns) >= 4 else 0.0

        # SOL local short-term return over last 3 bars
        sol_ret_3 = (closes[-1] - closes[-4]) / closes[-4] if closes[-4] != 0 else 0.0

        # Dislocation check: BTC made a statistically significant fresh move, but SOL lagged
        # Z-score > 2.2 ensures highly selective entry frequency across 17,000 bars (~30-80 trades)
        long_catchup = (btc_zscore > 2.2) and (fresh_btc_move > 0.4) and (sol_ret_3 < fresh_btc_move * 0.3)
        short_catchup = (btc_zscore < -2.2) and (fresh_btc_move < -0.4) and (sol_ret_3 > fresh_btc_move * 0.3)

        if long_catchup:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=120.0,
                take_profit_bps=180.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "sol_btc_lag_long",
                    "btc_zscore": round(btc_zscore, 2),
                    "fresh_btc_move": round(fresh_btc_move, 3),
                    "sol_ret_3": round(sol_ret_3, 3),
                }
            )

        if short_catchup:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=120.0,
                take_profit_bps=180.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "sol_btc_lag_short",
                    "btc_zscore": round(btc_zscore, 2),
                    "fresh_btc_move": round(fresh_btc_move, 3),
                    "sol_ret_3": round(sol_ret_3, 3),
                }
            )

        return None