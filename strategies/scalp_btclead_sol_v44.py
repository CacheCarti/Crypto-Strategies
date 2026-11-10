from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetBetaLag(Strategy):
    METADATA = {
        "name": "SolCrossAssetBetaLag",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.max_hold_bars = 5
        self.prev_btc_ret = None
        self.btc_ret_history = []

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)
        if len(closes) < 35:
            return None

        current_btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append(current_btc_ret)
        if len(self.btc_ret_history) > 10:
            self.btc_ret_history.pop(0)

        # Handle active position exit via time/horizon rule
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_scalp_timeout", "bars_held": bars_held}
                )
            return None

        # Mandatory cooldown post-exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety filter: skip extreme crisis or high spread
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        if len(self.btc_ret_history) < 4:
            return None

        # Freshness: measure the 3-bar delta in BTC return to detect an impulse rather than stale trend
        btc_impulse = current_btc_ret - self.btc_ret_history[-4]

        # SOL local momentum over last 3 bars (in percent)
        sol_ret_3bar = ((closes[-1] - closes[-4]) / closes[-4]) * 100.0

        # Dislocation check: BTC surged/plunged sharply while SOL lagged behind
        # Threshold: BTC 3-bar shock >= 1.0% while SOL moved less than half
        min_shock = 0.95
        lag_margin = 0.45

        atr = self._atr(highs, lows, closes, 14)
        atr_pct = (atr / closes[-1]) * 100.0 if closes[-1] > 0 else 0.5

        # Long: BTC sharply accelerated up, SOL has not yet matched the move
        if btc_impulse >= min_shock and sol_ret_3bar < (btc_impulse * lag_margin):
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.55 + (btc_impulse - min_shock) * 0.15)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_lag_long_impulse",
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_ret_3bar": round(sol_ret_3bar, 3),
                    "atr_pct": round(atr_pct, 3),
                }
            )

        # Short: BTC sharply accelerated down, SOL has not yet broken down
        if btc_impulse <= -min_shock and sol_ret_3bar > (btc_impulse * lag_margin):
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.55 + (abs(btc_impulse) - min_shock) * 0.15)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_lag_short_impulse",
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_ret_3bar": round(sol_ret_3bar, 3),
                    "atr_pct": round(atr_pct, 3),
                }
            )

        return None