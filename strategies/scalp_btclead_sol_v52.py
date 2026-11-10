from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.max_hold_bars = 4
        self.last_trade_bar = -999
        self.entry_bar = -999
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
        self.last_trade_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(20)
        lows = ctx.lows(20)

        if len(closes) < 25 or len(highs) < 15 or len(lows) < 15:
            return None

        current_btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append(current_btc_ret)
        if len(self.btc_ret_history) > 10:
            self.btc_ret_history.pop(0)

        # Time-based active exit for scalp catch-up
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            if bars_held >= self.max_hold_bars:
                direction = ctx.position_direction()
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "scalp_time_exit", "bars_held": bars_held, "direction": direction}
                )
            return None

        # Mandatory post-trade cooldown to limit overtrading and friction
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Need sufficient history to measure BTC return impulse freshness
        if len(self.btc_ret_history) < 4:
            return None

        # Calculate 3-bar delta in 24h return to detect an immediate sharp BTC thrust
        btc_thrust = self.btc_ret_history[-1] - self.btc_ret_history[-4]

        # Calculate SOL's own move over the identical 3-bar window (15 mins)
        sol_ret_3bar = (closes[-1] - closes[-4]) / closes[-4] * 100.0

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        # Avoid entry during extreme panic/crisis where cross-asset beta breaks down
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Thresholds: sharp impulse in BTC (>0.85% in 15m) with SOL lagging significantly
        btc_threshold = 0.85
        lag_diff = 0.55

        # Long Setup: BTC surges upward, SOL has lagged
        if btc_thrust > btc_threshold and (btc_thrust - sol_ret_3bar) > lag_diff:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=75.0,
                take_profit_bps=120.0,
                horizon_seconds=900,
                metadata={
                    "reason": "btc_lead_sol_lag_bullish",
                    "btc_thrust": round(btc_thrust, 3),
                    "sol_ret_3bar": round(sol_ret_3bar, 3),
                    "lag_diff": round(btc_thrust - sol_ret_3bar, 3),
                    "atr": round(atr, 4)
                }
            )

        # Short Setup: BTC drops sharply, SOL has lagged
        if btc_thrust < -btc_threshold and (sol_ret_3bar - btc_thrust) > lag_diff:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=75.0,
                take_profit_bps=120.0,
                horizon_seconds=900,
                metadata={
                    "reason": "btc_lead_sol_lag_bearish",
                    "btc_thrust": round(btc_thrust, 3),
                    "sol_ret_3bar": round(sol_ret_3bar, 3),
                    "lag_diff": round(sol_ret_3bar - btc_thrust, 3),
                    "atr": round(atr, 4)
                }
            )

        return None