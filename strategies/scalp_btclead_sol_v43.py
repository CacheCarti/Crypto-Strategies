from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcLeadSolBetaCatchup(Strategy):
    METADATA = {
        "name": "BtcLeadSolBetaCatchup",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.max_hold_bars = 5
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.btc_ret_history = []

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(25)
        if len(closes) < 25:
            return None

        current_btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append(current_btc_ret)
        if len(self.btc_ret_history) > 30:
            self.btc_ret_history.pop(0)

        if len(self.btc_ret_history) < 6:
            return None

        # Manage open position
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            if bars_in_trade >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_scalp", "bars_held": bars_in_trade}
                )
            return None

        # Cooldown guard to avoid friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime filter: Prior failures show edge only works during high volatility/transitions
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN" or ctx.regime == "crisis":
            return None

        # Freshness of BTC move: sharp impulse over the last 3 bars
        btc_3bar_delta = current_btc_ret - self.btc_ret_history[-4]

        # SOL local return over last 3 bars in bps
        sol_ret_3bar = (closes[-1] - closes[-4]) / closes[-4] * 100.0

        # Dislocation trigger: BTC accelerated sharply, SOL has lagged significantly
        # BTC delta > 1.4% with SOL moving under 0.35% (or inverse for shorts)
        long_setup = (btc_3bar_delta >= 1.35) and (sol_ret_3bar <= 0.35) and (current_btc_ret > 0.8)
        short_setup = (btc_3bar_delta <= -1.35) and (sol_ret_3bar >= -0.35) and (current_btc_ret < -0.8)

        if not (long_setup or short_setup):
            return None

        if long_setup:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.80,
                stop_loss_bps=75.0,
                take_profit_bps=160.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_expansion_lag",
                    "btc_delta_3b": round(btc_3bar_delta, 3),
                    "sol_ret_3b": round(sol_ret_3bar, 3),
                    "btc_ret": round(current_btc_ret, 3),
                    "regime": regime
                }
            )

        if short_setup:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.80,
                stop_loss_bps=75.0,
                take_profit_bps=160.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_dump_lag",
                    "btc_delta_3b": round(btc_3bar_delta, 3),
                    "sol_ret_3b": round(sol_ret_3bar, 3),
                    "btc_ret": round(current_btc_ret, 3),
                    "regime": regime
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index