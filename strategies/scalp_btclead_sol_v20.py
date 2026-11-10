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
        self.entry_bar = 0
        self.btc_window = 24
        self.sol_window = 6
        self.min_bars_hold = 3
        self.max_bars_hold = 6

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def _stddev(self, values: list) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        mean = sum(values) / n
        var = sum((x - mean) ** 2 for x in values) / (n - 1)
        return math.sqrt(var)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()
            
            # Time-based scalp exit
            if bars_held >= self.max_bars_hold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_timeout", "bars_held": bars_held}
                )

            # Fast momentum exhaustion exit
            if bars_held >= self.min_bars_hold:
                sol_ret_1bar = (closes[-1] - closes[-2]) / closes[-2]
                if pos_dir == "long" and sol_ret_1bar < -0.003:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "long_momentum_fade", "ret": sol_ret_1bar}
                    )
                elif pos_dir == "short" and sol_ret_1bar > 0.003:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "short_momentum_fade", "ret": sol_ret_1bar}
                    )
            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Compute dynamic BTC impulse and SOL relative response
        btc_ret_raw = ctx.features.get("btc_return_pct", 0.0)
        btc_ret = float(btc_ret_raw)

        # Measure SOL returns over the recent micro-window
        sol_ret_recent = (closes[-1] - closes[-self.sol_window]) / closes[-self.sol_window]
        
        # Calculate rolling volatility of SOL 1-bar returns for z-scoring
        sol_1bar_returns = [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(len(closes) - self.btc_window, len(closes))
        ]
        sol_vol = self._stddev(sol_1bar_returns)
        if sol_vol < 1e-5:
            return None

        # Dynamic cross-asset divergence metrics
        # BTC significant surge while SOL is lagging
        btc_magnitude = abs(btc_ret)
        is_bull_divergence = (btc_ret >= 1.5) and (sol_ret_recent < 0.005)
        is_bear_divergence = (btc_ret <= -1.5) and (sol_ret_recent > -0.005)

        # Secondary micro-structure filter: bar expansion confirmation
        curr_bar = ctx.bar
        bar_range = curr_bar.high - curr_bar.low
        prior_ranges = [ctx.highs(10)[i] - ctx.lows(10)[i] for i in range(9)]
        avg_range = (sum(prior_ranges) / len(prior_ranges)) if prior_ranges else bar_range

        if bar_range < 0.5 * avg_range:
            return None

        if is_bull_divergence:
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.5 + (btc_magnitude - 1.5) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_bull",
                    "btc_ret": btc_ret,
                    "sol_ret_recent": sol_ret_recent,
                    "sol_vol": sol_vol,
                }
            )

        if is_bear_divergence:
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.5 + (btc_magnitude - 1.5) * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_bear",
                    "btc_ret": btc_ret,
                    "sol_ret_recent": sol_ret_recent,
                    "sol_vol": sol_vol,
                }
            )

        return None