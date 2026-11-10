from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcLagScalp(Strategy):
    METADATA = {
        "name": "SolBtcLagScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,  # ~4 bars (20 mins)
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.max_hold_bars = 5
        self.entry_bar = -1
        self.last_exit_bar = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(25)
        highs = ctx.highs(25)
        lows = ctx.lows(25)

        if len(closes) < 20:
            return None

        # Position management: time-based exit for short-lived lag play
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            if bars_in_trade >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "time_exit_reached", "bars_held": bars_in_trade})
            return None

        # Mandatory cooldown after any exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market stress gate: avoid trading during full meltdown/crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Cross-asset trigger: BTC 24h return metric
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # SOL local return over last 3 bars (15 mins) and 12 bars (1 hour)
        sol_ret_15m = (closes[-1] - closes[-4]) / closes[-4] * 100.0
        sol_ret_1h = (closes[-1] - closes[-13]) / closes[-13] * 100.0

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or closes[-1] == 0:
            return None
        atr_pct = (atr / closes[-1]) * 100.0

        # We look for a distinct impulse in BTC (> 1.2%) where SOL has underperformed or lagged
        # Long catch-up: BTC is strong, SOL has not yet expanded in the last 15m
        long_cond = (btc_ret >= 1.25) and (sol_ret_15m < 0.25) and (sol_ret_1h < 1.0) and (atr_pct >= 0.15)

        # Short catch-up: BTC is weak, SOL has not dumped yet
        short_cond = (btc_ret <= -1.25) and (sol_ret_15m > -0.25) and (sol_ret_1h > -1.0) and (atr_pct >= 0.15)

        if long_cond:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_return_pct": btc_ret,
                    "sol_ret_15m": round(sol_ret_15m, 3),
                    "sol_ret_1h": round(sol_ret_1h, 3),
                    "atr_pct": round(atr_pct, 3),
                }
            )

        if short_cond:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_return_pct": btc_ret,
                    "sol_ret_15m": round(sol_ret_15m, 3),
                    "sol_ret_1h": round(sol_ret_1h, 3),
                    "atr_pct": round(atr_pct, 3),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index