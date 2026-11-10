from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 1200,   # 4 bars @ 5m
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.max_hold_bars = 4
        self.entry_bar_idx = -1
        self.last_exit_bar = -100
        self.btc_ret_history = []

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
        self.entry_bar_idx = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 30:
            return None

        # Track btc_return_pct locally to detect fresh impulse
        current_btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append(current_btc_ret)
        if len(self.btc_ret_history) > 10:
            self.btc_ret_history.pop(0)

        # Time-based exit management
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar_idx
            if self.entry_bar_idx > 0 and bars_in_pos >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_horizon_reached", "bars_held": bars_in_pos}
                )
            return None

        # Cooldown guard after exit to prevent overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid extreme breakdown states
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        if len(self.btc_ret_history) < 4:
            return None

        # Measure 3-bar delta in BTC return to ensure move is fresh (impulsive burst)
        btc_3bar_delta = current_btc_ret - self.btc_ret_history[-4]
        
        # Measure SOL return over the same 3-bar lookback
        sol_ret_3bar = ((closes[-1] - closes[-4]) / closes[-4]) * 100.0

        # Dislocation check: BTC made a rapid >0.9% burst, but SOL has severely lagged (<0.25%)
        # This keeps entries rare (~40-80 trades in 17,000 bars) and rejects marginal drift.
        long_dislocation = (btc_3bar_delta >= 0.90) and (sol_ret_3bar < 0.25)
        short_dislocation = (btc_3bar_delta <= -0.90) and (sol_ret_3bar > -0.25)

        if not (long_dislocation or short_dislocation):
            return None

        # Volatility check: require calm-to-moderate local SOL ATR to avoid entries mid-liquidation wick
        atr = self._atr(highs, lows, closes, 14)
        if atr is None:
            return None
        atr_bps = (atr / closes[-1]) * 10000.0
        if atr_bps > 180.0:
            return None

        if long_dislocation:
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.80,
                stop_loss_bps=75.0,
                take_profit_bps=120.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_delta_3b": round(btc_3bar_delta, 3),
                    "sol_ret_3b": round(sol_ret_3bar, 3),
                    "sol_atr_bps": round(atr_bps, 1),
                }
            )

        if short_dislocation:
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.80,
                stop_loss_bps=75.0,
                take_profit_bps=120.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_delta_3b": round(btc_3bar_delta, 3),
                    "sol_ret_3b": round(sol_ret_3bar, 3),
                    "sol_atr_bps": round(atr_bps, 1),
                }
            )

        return None