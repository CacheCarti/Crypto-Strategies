from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcSolBetaLagScalp(Strategy):
    METADATA = {
        "name": "BtcSolBetaLagScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 190.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_returns = []
        self.entry_bar = -999
        self.last_exit_bar = -999
        self.max_hold_bars = 5
        self.cooldown_bars = 8
        self.lookback_delta_bars = 3
        self.btc_delta_threshold = 1.05
        self.sol_lag_threshold_ratio = 0.35

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / float(period)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_returns.append(btc_ret)
        if len(self.btc_returns) > 30:
            self.btc_returns.pop(0)

        closes = ctx.closes(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(15)

        if len(closes) < self.METADATA["warmup_bars"] or len(self.btc_returns) <= self.lookback_delta_bars:
            return None

        # Position management: time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_horizon_reached",
                        "bars_held": bars_held,
                        "exit_price": ctx.bar.close,
                        "btc_return_pct": btc_ret,
                    },
                )
            return None

        # Cooldown guard: prevent overtrading and fee erosion
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Volume filter: ensure active market participation
        avg_vol = self._sma(volumes, 12)
        if avg_vol and ctx.bar.volume < avg_vol * 0.5:
            return None

        # Calculate BTC return delta over 15 minutes (3 bars)
        btc_delta = self.btc_returns[-1] - self.btc_returns[-1 - self.lookback_delta_bars]

        # Calculate SOL return over the same lookback period
        sol_past_close = closes[-1 - self.lookback_delta_bars]
        sol_current_close = closes[-1]
        sol_ret_pct = ((sol_current_close - sol_past_close) / sol_past_close) * 100.0

        # Long Setup: BTC accelerated upward, SOL hasn't caught up
        if btc_delta >= self.btc_delta_threshold and sol_ret_pct <= (self.btc_delta_threshold * self.sol_lag_threshold_ratio):
            self.entry_bar = ctx.bar_index
            conf = min(0.90, max(0.60, 0.60 + (btc_delta - self.btc_delta_threshold) * 0.2))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_catchup_long",
                    "btc_delta_15m": round(btc_delta, 3),
                    "sol_ret_15m": round(sol_ret_pct, 3),
                    "btc_return_pct": round(btc_ret, 3),
                    "sol_price": sol_current_close,
                },
            )

        # Short Setup: BTC accelerated downward, SOL hasn't caught down
        if btc_delta <= -self.btc_delta_threshold and sol_ret_pct >= (-self.btc_delta_threshold * self.sol_lag_threshold_ratio):
            self.entry_bar = ctx.bar_index
            conf = min(0.90, max(0.60, 0.60 + (abs(btc_delta) - self.btc_delta_threshold) * 0.2))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_catchup_short",
                    "btc_delta_15m": round(btc_delta, 3),
                    "sol_ret_15m": round(sol_ret_pct, 3),
                    "btc_return_pct": round(btc_ret, 3),
                    "sol_price": sol_current_close,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index