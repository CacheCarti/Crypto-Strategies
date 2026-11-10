from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcLagCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcLagCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1500,  # ~5 bars of 5m
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_until_bar = 0
        self.entry_bar = 0
        self.max_hold_bars = 5
        self.cooldown_period = 10
        self.btc_ret_history = []

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + self.cooldown_period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        # Track BTC 24h return to capture the impulse (first-difference / freshness)
        curr_btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append(curr_btc_ret)
        if len(self.btc_ret_history) > 15:
            self.btc_ret_history.pop(0)

        # Handle active position exit (time-based horizon guard)
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_catchup_elapsed", "bars_held": bars_held}
                )
            return None

        # Check cooldown
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Avoid extreme crisis / meltdown regime noise
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Freshness check: BTC impulse over the last 3 bars (15 mins)
        if len(self.btc_ret_history) < 4:
            return None
        btc_impulse = curr_btc_ret - self.btc_ret_history[-4]

        # SOL local short-term return over the same 3 bars
        sol_ret_3bar = (closes[-1] - closes[-4]) / closes[-4] * 100.0

        # High-conviction entry conditions:
        # Require BTC impulse >= 1.6% while SOL has lagged significantly (has not fully caught up yet)
        min_impulse = 1.6
        lag_margin = 0.8  # BTC moved at least 0.8% more than SOL recently

        long_trigger = (btc_impulse >= min_impulse) and (btc_impulse - sol_ret_3bar >= lag_margin)
        short_trigger = (btc_impulse <= -min_impulse) and (sol_ret_3bar - btc_impulse >= lag_margin)

        if long_trigger:
            self.entry_bar = ctx.bar_index
            self.cooldown_until_bar = ctx.bar_index + self.cooldown_period
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=95.0,
                take_profit_bps=160.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_sol_lag_bullish_impulse",
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_ret_3bar": round(sol_ret_3bar, 3),
                    "close": closes[-1],
                }
            )

        if short_trigger:
            self.entry_bar = ctx.bar_index
            self.cooldown_until_bar = ctx.bar_index + self.cooldown_period
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=95.0,
                take_profit_bps=160.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_sol_lag_bearish_impulse",
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_ret_3bar": round(sol_ret_3bar, 3),
                    "close": closes[-1],
                }
            )

        return None