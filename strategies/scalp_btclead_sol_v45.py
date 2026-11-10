from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 5

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < 40:
            return None

        # Manage existing position exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                pos_dir = ctx.position_direction()
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_scalp_horizon", "bars_held": bars_held, "direction": pos_dir}
                )
            return None

        # Check post-exit cooldown
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Regime / Crisis check (filter out extreme panic / meltdown)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Feature checks: BTC 24h return momentum and freshness
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        sol_ret = ctx.features.get("sol_return_pct", 0.0)

        # Catch-up divergence: BTC moving sharply while SOL lags in rolling 24h context
        # SOL short-term return (last 3 bars = 15m)
        sol_15m_ret_bps = ((closes[-1] - closes[-4]) / closes[-4]) * 10000.0

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or closes[-1] == 0:
            return None
        atr_bps = (atr / closes[-1]) * 10000.0

        # Require a moderately calm to active volatility state (not dead, not ultra-blown out)
        if atr_bps < 10.0 or atr_bps > 120.0:
            return None

        # Trigger logic: BTC has strong directional push (> 1.6%) and leads SOL return gap > 1.2%
        # While SOL has not yet overextended in the 15m window (lagging behind)
        long_setup = (btc_ret >= 1.6) and ((btc_ret - sol_ret) >= 1.2) and (sol_15m_ret_bps < 40.0)
        short_setup = (btc_ret <= -1.6) and ((sol_ret - btc_ret) >= 1.2) and (sol_15m_ret_bps > -40.0)

        if long_setup:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=120.0,
                take_profit_bps=220.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_ret": btc_ret,
                    "sol_ret": sol_ret,
                    "sol_15m_ret_bps": round(sol_15m_ret_bps, 2),
                    "atr_bps": round(atr_bps, 2)
                }
            )

        if short_setup:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=120.0,
                take_profit_bps=220.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_ret": btc_ret,
                    "sol_ret": sol_ret,
                    "sol_15m_ret_bps": round(sol_15m_ret_bps, 2),
                    "atr_bps": round(atr_bps, 2)
                }
            )

        return None