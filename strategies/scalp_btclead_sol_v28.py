from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcSolBetaLagScalp(Strategy):
    METADATA = {
        "name": "BtcSolBetaLagScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.max_hold_bars = 5
        self.last_exit_bar = -999
        self.entry_bar_idx = -1
        self.btc_window = 3
        self.sol_window = 3
        self.recent_btc_returns = []

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
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        
        if len(closes) < 35:
            return None

        # Track btc_return_pct rolling history
        curr_btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.recent_btc_returns.append(curr_btc_ret)
        if len(self.recent_btc_returns) > 20:
            self.recent_btc_returns.pop(0)

        # Timed exit logic to prevent stale beta drag
        if ctx.has_position():
            if self.entry_bar_idx > 0 and (ctx.bar_index - self.entry_bar_idx >= self.max_hold_bars):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "timed_scalp_horizon_expired",
                        "bars_held": ctx.bar_index - self.entry_bar_idx,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime filter: avoid taking catchup scalps during extreme crisis
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime == "MELTDOWN" or crisis_score > 0.65:
            return None

        if len(self.recent_btc_returns) < self.btc_window + 1:
            return None

        # Calculate impulse over recent window
        btc_delta_3b = self.recent_btc_returns[-1] - self.recent_btc_returns[-1 - self.btc_window]
        sol_ret_3b = ((closes[-1] - closes[-1 - self.sol_window]) / closes[-1 - self.sol_window]) * 100.0

        # Volatility-based adaptive stop check
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or closes[-1] == 0:
            return None
        atr_bps = (atr / closes[-1]) * 10000.0
        stop_bps = max(70.0, min(140.0, atr_bps * 1.25))
        tp_bps = stop_bps * 1.6

        # Cross-asset lag condition:
        # BTC exhibits a sharp move (> 1.25%) while SOL has significantly lagged (move < 40% of BTC)
        is_fresh_long = (btc_delta_3b >= 1.25) and (sol_ret_3b < btc_delta_3b * 0.35)
        is_fresh_short = (btc_delta_3b <= -1.25) and (sol_ret_3b > btc_delta_3b * 0.35)

        if is_fresh_long:
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(0.85, 0.55 + abs(btc_delta_3b - sol_ret_3b) * 0.1),
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "btc_lead_sol_lag_bullish",
                    "btc_delta_3b": round(btc_delta_3b, 3),
                    "sol_ret_3b": round(sol_ret_3b, 3),
                    "atr_bps": round(atr_bps, 1),
                    "price": closes[-1]
                }
            )

        if is_fresh_short:
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=min(0.85, 0.55 + abs(btc_delta_3b - sol_ret_3b) * 0.1),
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "btc_lead_sol_lag_bearish",
                    "btc_delta_3b": round(btc_delta_3b, 3),
                    "sol_ret_3b": round(sol_ret_3b, 3),
                    "atr_bps": round(atr_bps, 1),
                    "price": closes[-1]
                }
            )

        return None