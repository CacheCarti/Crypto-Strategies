from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcBetaCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcBetaCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 85.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 5
        self.btc_window = 3
        self.sol_window = 3
        self.min_btc_move = 0.95
        self.max_sol_move = 0.35

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_exit_catchup_window_closed",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volatility check
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or (atr / ctx.bar.close) * 10000 < 15.0:
            return None

        # Measure SOL return over recent bars
        sol_ret = (closes[-1] - closes[-1 - self.sol_window]) / closes[-1 - self.sol_window] * 100.0

        # Feature reading: btc_return_pct is 24h return rolling; sharp delta captures fresh BTC moves
        btc_24h = ctx.features.get("btc_return_pct", 0.0)
        if not hasattr(self, "_btc_history"):
            self._btc_history = []
        self._btc_history.append(btc_24h)
        if len(self._btc_history) > 10:
            self._btc_history.pop(0)

        if len(self._btc_history) < self.btc_window + 1:
            return None

        btc_delta = self._btc_history[-1] - self._btc_history[-1 - self.btc_window]

        # Long Catch-up: BTC surged recently, but SOL lagged
        if btc_delta >= self.min_btc_move and sol_ret <= self.max_sol_move:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=85.0,
                take_profit_bps=160.0,
                horizon_seconds=900,
                metadata={
                    "reason": "btc_lead_lag_long_catchup",
                    "btc_delta": round(btc_delta, 3),
                    "sol_ret": round(sol_ret, 3),
                    "close": ctx.bar.close,
                    "atr": round(atr, 4),
                }
            )

        # Short Catch-up: BTC dumped recently, but SOL lagged
        if btc_delta <= -self.min_btc_move and sol_ret >= -self.max_sol_move:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=85.0,
                take_profit_bps=160.0,
                horizon_seconds=900,
                metadata={
                    "reason": "btc_lead_lag_short_catchup",
                    "btc_delta": round(btc_delta, 3),
                    "sol_ret": round(sol_ret, 3),
                    "close": ctx.bar.close,
                    "atr": round(atr, 4),
                }
            )

        return None