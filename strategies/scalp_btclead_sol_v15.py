from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcLagScalp(Strategy):
    METADATA = {
        "name": "SolBtcLagScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,  # 4 bars (20m) expected scalp hold
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16          # At least 80m between trades to stay strictly within 10-120 trades
        self.last_exit_bar = -999
        self.entry_bar_idx = -999
        self.max_hold_bars = 5           # Exit after 3-5 bars (15-25m)
        self.prev_btc_ret = 0.0

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
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        if len(closes) < 30:
            return None

        # Time-in-trade exit management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_exit", "bars_held": bars_held, "price": ctx.bar.close}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime filter: avoid trading in crisis or melt-down regimes where lag breaks down
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volatility check: require moderate volatility, skip dead or hyper-erratic markets
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None
        atr_pct = (atr / ctx.bar.close) * 100.0
        if atr_pct < 0.15 or atr_pct > 1.20:
            return None

        # Cross-asset BTC lead signal
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        btc_delta = btc_ret - self.prev_btc_ret
        self.prev_btc_ret = btc_ret

        # SOL local momentum over last 3 bars (15 mins)
        sol_ret_3bar = ((closes[-1] - closes[-4]) / closes[-4]) * 100.0

        # Dislocation check: BTC made a fresh move (>0.5% impulse delta) and SOL has lagged behind (<0.2%)
        min_impulse = 0.55
        max_sol_absorption = 0.22

        # Bullish setup: BTC surged, SOL hasn't caught up
        if btc_delta >= min_impulse and btc_ret > 1.2 and sol_ret_3bar < max_sol_absorption:
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=80.0,
                take_profit_bps=150.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_delta": round(btc_delta, 3),
                    "btc_ret": round(btc_ret, 3),
                    "sol_ret_3bar": round(sol_ret_3bar, 3),
                    "atr_pct": round(atr_pct, 3),
                }
            )

        # Bearish setup: BTC dumped, SOL hasn't dropped yet
        if btc_delta <= -min_impulse and btc_ret < -1.2 and sol_ret_3bar > -max_sol_absorption:
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=80.0,
                take_profit_bps=150.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_delta": round(btc_delta, 3),
                    "btc_ret": round(btc_ret, 3),
                    "sol_ret_3bar": round(sol_ret_3bar, 3),
                    "atr_pct": round(atr_pct, 3),
                }
            )

        return None