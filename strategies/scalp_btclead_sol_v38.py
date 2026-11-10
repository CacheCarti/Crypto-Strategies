from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcLagCatchup(Strategy):
    METADATA = {
        "name": "SolBtcLagCatchup",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 5
        self.btc_threshold = 1.35
        self.lag_threshold_pct = 0.55

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
        if len(closes) < 25:
            return None

        current_idx = ctx.bar_index
        has_pos = ctx.has_position()

        # Exit logic based on time-in-trade structure
        if has_pos:
            bars_held = current_idx - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_trade_bar = current_idx
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "timed_scalp_exhaustion", "bars_held": bars_held}
                )
            return None

        # Mandatory Cooldown Gate
        if current_idx - self.last_trade_bar < self.cooldown_bars:
            return None

        # Cross-asset BTC feature
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        if abs(btc_ret) < self.btc_threshold:
            return None

        # Regime safety filter: skip Meltdown or extreme crisis
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime == "MELTDOWN" or crisis_score > 0.8:
            return None

        # Measure SOL's short-term reaction (past 3 bars = 15m)
        sol_p0 = closes[-4]
        sol_p_now = closes[-1]
        sol_ret_15m = ((sol_p_now - sol_p0) / sol_p0) * 100.0

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or sol_p_now == 0:
            return None

        # Dynamic volatility stop calibration
        atr_bps = (atr / sol_p_now) * 10000.0
        stop_loss_bps = max(65.0, min(140.0, atr_bps * 1.1))
        take_profit_bps = max(110.0, min(220.0, stop_loss_bps * 1.6))

        # Long scenario: BTC surged sharply, SOL lagged behind
        if btc_ret >= self.btc_threshold and sol_ret_15m < self.lag_threshold_pct:
            self.last_trade_bar = current_idx
            self.entry_bar = current_idx
            conf = min(0.9, 0.55 + (btc_ret - self.btc_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_bull",
                    "btc_return_pct": btc_ret,
                    "sol_return_15m": sol_ret_15m,
                    "atr_bps": atr_bps,
                    "stop_loss_bps": stop_loss_bps,
                }
            )

        # Short scenario: BTC dropped sharply, SOL has not yet caught down
        if btc_ret <= -self.btc_threshold and sol_ret_15m > -self.lag_threshold_pct:
            self.last_trade_bar = current_idx
            self.entry_bar = current_idx
            conf = min(0.9, 0.55 + (abs(btc_ret) - self.btc_threshold) * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_bear",
                    "btc_return_pct": btc_ret,
                    "sol_return_15m": sol_ret_15m,
                    "atr_bps": atr_bps,
                    "stop_loss_bps": stop_loss_bps,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        # If stopped out with a loss, expand cooldown slightly to avoid re-triggering into whipsaw
        if position.get("return_bps", 0.0) < -40.0:
            self.last_trade_bar += 6