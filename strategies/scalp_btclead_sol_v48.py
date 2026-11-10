from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetBetaCatchup(Strategy):
    METADATA = {
        "name": "SolBtcBetaCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1200,  # ~4 bars (20 mins)
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.max_hold_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.btc_window = 3

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
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < 35:
            return None

        # Manage active position: strict timed exit or trailing guard
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "timed_scalp_horizon_reached",
                    "bars_held": bars_held,
                    "close": ctx.bar.close
                })
            return None

        # Cooldown guard to eliminate friction overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid crisis/illiquid states
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Cross-asset trigger: BTC 24h return momentum surge over last few bars
        btc_now = ctx.features.get("btc_return_pct", 0.0)
        # btc_return_pct is 24h rolling % return on per-bar cadence.
        # A sharp move means the absolute level is strong AND fresh change is non-zero.
        if abs(btc_now) < 1.4:
            return None

        # Compute SOL recent return over the shock window (3 bars = 15m)
        sol_ret_window = (closes[-1] - closes[-self.btc_window]) / closes[-self.btc_window] * 100.0

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None
        atr_bps = (atr / closes[-1]) * 10000.0

        # Dynamic volatility-anchored stops
        sl_bps = max(70.0, min(160.0, atr_bps * 1.5))
        tp_bps = max(110.0, min(280.0, atr_bps * 2.5))

        # Long Catch-Up: BTC up hard, SOL has lagged significantly behind
        if btc_now >= 1.4 and sol_ret_window < (btc_now * 0.35):
            # Price action confirmation: bar must not be an exhaustive reversal candle
            if closes[-1] >= lows[-1] + (highs[-1] - lows[-1]) * 0.25:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.55 + min(abs(btc_now - sol_ret_window) * 0.1, 0.35))
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=1200,
                    metadata={
                        "reason": "btc_beta_catchup_long",
                        "btc_return": round(btc_now, 3),
                        "sol_window_ret": round(sol_ret_window, 3),
                        "lag_bps": round((btc_now - sol_ret_window) * 100.0, 1),
                        "atr_bps": round(atr_bps, 1)
                    }
                )

        # Short Catch-Up: BTC down hard, SOL has not yet absorbed the dump
        elif btc_now <= -1.4 and sol_ret_window > (btc_now * 0.35):
            if closes[-1] <= highs[-1] - (highs[-1] - lows[-1]) * 0.25:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.55 + min(abs(sol_ret_window - btc_now) * 0.1, 0.35))
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=1200,
                    metadata={
                        "reason": "btc_beta_catchup_short",
                        "btc_return": round(btc_now, 3),
                        "sol_window_ret": round(sol_ret_window, 3),
                        "lag_bps": round((sol_ret_window - btc_now) * 100.0, 1),
                        "atr_bps": round(atr_bps, 1)
                    }
                )

        return None