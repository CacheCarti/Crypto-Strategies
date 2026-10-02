from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcSolBetaCatchupScalp(Strategy):
    METADATA = {
        "name": "BTC-SOL Beta Catchup Scalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 150.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 4
        self.cooldown_bars = 8
        self.max_hold_bars = 5
        self.btc_threshold = 0.65
        self.sol_max_move = 0.20
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.btc_hist = []

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -100

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 1)
        if len(closes) < self.lookback_bars + 1:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_hist.append(btc_ret)
        if len(self.btc_hist) > 50:
            self.btc_hist.pop(0)

        if len(self.btc_hist) <= self.lookback_bars:
            return None

        # Freshness of BTC move over lookback window (4 bars = 20 mins)
        btc_delta = self.btc_hist[-1] - self.btc_hist[-1 - self.lookback_bars]

        # SOL price move over the exact same window
        sol_ret = ((closes[-1] - closes[-1 - self.lookback_bars]) / closes[-1 - self.lookback_bars]) * 100.0

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_bars_time_exit",
                        "bars_held": bars_held,
                        "btc_delta": round(btc_delta, 4),
                        "sol_ret": round(sol_ret, 4),
                    },
                )
            return None

        # Entry cooldown gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Cross-asset lag long: BTC surged sharply, SOL has not yet responded
        if btc_delta >= self.btc_threshold and sol_ret <= self.sol_max_move:
            lag = btc_delta - sol_ret
            conf = min(0.9, max(0.55, 0.5 + lag * 0.3))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=90.0,
                take_profit_bps=150.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_delta": round(btc_delta, 4),
                    "sol_ret": round(sol_ret, 4),
                    "lag": round(lag, 4),
                    "price": closes[-1],
                },
            )

        # Cross-asset lag short: BTC plunged sharply, SOL has not yet responded
        if btc_delta <= -self.btc_threshold and sol_ret >= -self.sol_max_move:
            lag = abs(btc_delta) - abs(sol_ret)
            conf = min(0.9, max(0.55, 0.5 + lag * 0.3))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=90.0,
                take_profit_bps=150.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_delta": round(btc_delta, 4),
                    "sol_ret": round(sol_ret, 4),
                    "lag": round(lag, 4),
                    "price": closes[-1],
                },
            )

        return None