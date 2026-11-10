from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetBetaCatchup(Strategy):
    METADATA = {
        "name": "SolCrossAssetBetaCatchup",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1500,  # ~5 bars (25 mins) max expected
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_trade_bar = -100
        self.entry_bar = 0
        self.max_hold_bars = 5
        self.min_btc_move = 1.2
        self.btc_window = 3

    def _stddev(self, values: list) -> float:
        if len(values) < 2:
            return 1.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        current_bar = ctx.bar_index
        pos_dir = ctx.position_direction()

        # Manage existing position: time-based exit to capture quick lag
        if pos_dir is not None:
            bars_held = current_bar - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_trade_bar = current_bar
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_exit_catchup_completed", "bars_held": bars_held}
                )
            return None

        # Post-trade cooldown
        if (current_bar - self.last_trade_bar) < self.cooldown_bars:
            return None

        # BTC return feature (24h rolling return % provided per bar)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        if btc_ret == 0.0:
            return None

        # Calculate SOL's short-term return over the last few bars
        sol_ret_3bar = ((closes[-1] - closes[-1 - self.btc_window]) / closes[-1 - self.btc_window]) * 100.0

        # Calculate standard deviation of 3-bar returns to normalize SOL movement
        ret_diffs = [
            ((closes[i] - closes[i - self.btc_window]) / closes[i - self.btc_window]) * 100.0
            for i in range(self.btc_window, len(closes))
        ]
        sol_vol = self._stddev(ret_diffs)
        if sol_vol < 0.05:
            sol_vol = 0.05

        sol_z = sol_ret_3bar / sol_vol

        # Cross-asset lag detection:
        # BTC is exhibiting a strong impulse, but SOL has not yet matched the magnitude (|sol_z| < 1.0)
        # Avoid stale moves or when market is already in meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Bullish beta catchup: BTC surging, SOL lagging behind
        if btc_ret > self.min_btc_move and sol_z < 0.8:
            self.entry_bar = current_bar
            self.last_trade_bar = current_bar
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_return_pct": btc_ret,
                    "sol_ret_3bar": sol_ret_3bar,
                    "sol_z": sol_z,
                }
            )

        # Bearish beta catchup: BTC dumping, SOL lagging behind
        if btc_ret < -self.min_btc_move and sol_z > -0.8:
            self.entry_bar = current_bar
            self.last_trade_bar = current_bar
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_return_pct": btc_ret,
                    "sol_ret_3bar": sol_ret_3bar,
                    "sol_z": sol_z,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index