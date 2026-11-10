from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
from collections import deque

class BtcSolBetaLagScalp(Strategy):
    METADATA = {
        "name": "BtcSolBetaLagScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 140.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.entry_price = 0.0
        self.max_hold_bars = 4
        self.btc_ret_hist = deque(maxlen=6)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_hist.append(btc_ret)

        current_price = ctx.bar.close

        # Manage open position hold time
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_scalp_horizon", "bars_held": bars_held, "price": current_price}
                )
            return None

        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Need at least 4 historical BTC return readings to detect freshness
        if len(self.btc_ret_hist) < 4:
            return None

        prev_btc_ret = self.btc_ret_hist[-4]
        btc_accel = btc_ret - prev_btc_ret

        # SOL local move over the same 3 bars (15 minutes)
        sol_ret_15m = (closes[-1] - closes[-4]) / closes[-4] * 100.0

        # Filter out extreme crisis regimes where beta spreads can become unhinged
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Trigger: BTC 24h return jumped sharply (> +1.3% over 15m) or large baseline move (> +1.8%),
        # while SOL has lagged significantly (sol_ret_15m < 0.35%)
        long_setup = (btc_accel > 1.3 or (btc_ret > 1.8 and btc_accel > 0.8)) and (sol_ret_15m < 0.35)

        # Short Trigger: BTC 24h return dropped sharply (< -1.3% over 15m) or large baseline drop (< -1.8%),
        # while SOL has lagged significantly (sol_ret_15m > -0.35%)
        short_setup = (btc_accel < -1.3 or (btc_ret < -1.8 and btc_accel < -0.8)) and (sol_ret_15m > -0.35)

        if long_setup:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            self.entry_price = current_price
            conf = min(0.9, 0.65 + abs(btc_accel) * 0.08)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=75.0,
                take_profit_bps=140.0,
                horizon_seconds=900,
                metadata={
                    "reason": "btc_beta_lag_long_impulse",
                    "btc_ret": btc_ret,
                    "btc_accel": btc_accel,
                    "sol_ret_15m": sol_ret_15m,
                    "price": current_price,
                }
            )

        if short_setup:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            self.entry_price = current_price
            conf = min(0.9, 0.65 + abs(btc_accel) * 0.08)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=75.0,
                take_profit_bps=140.0,
                horizon_seconds=900,
                metadata={
                    "reason": "btc_beta_lag_short_impulse",
                    "btc_ret": btc_ret,
                    "btc_accel": btc_accel,
                    "sol_ret_15m": sol_ret_15m,
                    "price": current_price,
                }
            )

        return None