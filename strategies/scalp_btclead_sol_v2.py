from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math

class SolBtcBetaLagScalp(Strategy):
    METADATA = {
        "name": "SOL BTC Beta Lag Scalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 190.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_lag = 6          # 30 minutes (6 x 5m bars)
        self.btc_shock_threshold = 0.70  # % move in BTC over lookback
        self.sol_lag_max = 0.25         # Max SOL move % allowed to still be considered lagging
        self.max_hold_bars = 5          # 25 minutes target hold
        self.cooldown_bars = 16         # Cooldown between trades (~80 mins)
        self.last_exit_bar = -999
        self.entry_bar_idx = -1
        self.btc_history: List[float] = []

    def _atr(self, highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.lookback_lag + 20)
        highs = ctx.highs(20)
        lows = ctx.lows(20)

        if len(closes) < self.lookback_lag + 20 or len(highs) < 20 or len(lows) < 20:
            return None

        # Track BTC 24h return series to compute short-term BTC impulses
        current_btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_history.append(current_btc_ret)
        if len(self.btc_history) > 50:
            self.btc_history.pop(0)

        if len(self.btc_history) < self.lookback_lag + 1:
            return None

        # Handle active position management (time-based exit)
        if ctx.has_position():
            if self.entry_bar_idx > 0:
                bars_held = ctx.bar_index - self.entry_bar_idx
                if bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "time_horizon_exit",
                            "bars_held": bars_held,
                            "sol_close": ctx.bar.close,
                        }
                    )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime & Crisis safety filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime == "MELTDOWN":
            return None

        # Calculate short-term BTC return delta over lookback window
        btc_past = self.btc_history[-1 - self.lookback_lag]
        btc_delta = current_btc_ret - btc_past

        # Calculate SOL return over same lookback window
        sol_now = closes[-1]
        sol_past = closes[-1 - self.lookback_lag]
        if sol_past <= 0:
            return None
        sol_delta_pct = ((sol_now - sol_past) / sol_past) * 100.0

        # Volatility assessment for dynamic SL/TP
        atr = self._atr(highs, lows, closes, 14)
        atr_pct = (atr / sol_now * 10000.0) if atr and sol_now > 0 else 95.0
        sl_bps = max(70.0, min(140.0, atr_pct * 1.2))
        tp_bps = max(140.0, min(260.0, sl_bps * 1.8))

        # Long Setup: BTC surges upward, SOL is lagging behind
        if btc_delta >= self.btc_shock_threshold and sol_delta_pct <= self.sol_lag_max and sol_delta_pct >= -0.6:
            self.entry_bar_idx = ctx.bar_index
            conf = min(0.90, max(0.55, 0.55 + (btc_delta - self.btc_shock_threshold) * 0.25))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_delta_pct": round(btc_delta, 3),
                    "sol_delta_pct": round(sol_delta_pct, 3),
                    "btc_return_24h": round(current_btc_ret, 2),
                    "atr_bps": round(atr_pct, 1),
                }
            )

        # Short Setup: BTC drops sharply, SOL hasn't caught down yet
        if btc_delta <= -self.btc_shock_threshold and sol_delta_pct >= -self.sol_lag_max and sol_delta_pct <= 0.6:
            self.entry_bar_idx = ctx.bar_index
            conf = min(0.90, max(0.55, 0.55 + (abs(btc_delta) - self.btc_shock_threshold) * 0.25))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_delta_pct": round(btc_delta, 3),
                    "sol_delta_pct": round(sol_delta_pct, 3),
                    "btc_return_24h": round(current_btc_ret, 2),
                    "atr_bps": round(atr_pct, 1),
                }
            )

        return None