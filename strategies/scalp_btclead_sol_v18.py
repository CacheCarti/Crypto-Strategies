from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetBetaCatchup(Strategy):
    METADATA = {
        "name": "SolCrossAssetBetaCatchup",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.max_hold_bars = 4
        self.entry_bar = -999
        self.last_exit_bar = -999
        self.btc_window = 3
        self.sol_window = 3

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 30:
            return None

        # Check existing position for time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                current_p = ctx.bar.close
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "timed_scalp_exit", "bars_held": bars_held, "price": current_p}
                )
            return None

        # Cooldown guard after recent exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Feature reading: btc_return_pct (24h roll)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Approximate BTC delta over recent bars from btc_return_pct feature
        # Sol lag condition: calculate SOL's own move over the last 3 bars (15 mins)
        sol_recent_ret = (closes[-1] - closes[-self.sol_window]) / closes[-self.sol_window] * 100.0

        # Calculate volatility stop dynamic buffer
        atr = self._atr(highs, lows, closes, 14)
        if atr is None:
            return None
        atr_bps = (atr / closes[-1]) * 10000.0
        dynamic_sl = max(60.0, min(140.0, atr_bps * 1.2))
        dynamic_tp = max(110.0, min(240.0, atr_bps * 2.2))

        # Core logic: BTC significant move + SOL has NOT caught up yet (disparity/lag)
        # BTC move magnitude threshold: 1.5%
        btc_surge_long = btc_ret >= 1.5
        btc_dump_short = btc_ret <= -1.5

        # Avoid chasing if SOL has already moved > 0.6% in that direction
        # Require SOL to have compressed/consolidated relative to BTC impulse
        sol_lagging_bull = sol_recent_ret < 0.4 and sol_recent_ret > -0.8
        sol_lagging_bear = sol_recent_ret > -0.4 and sol_recent_ret < 0.8

        # Market regime filter: avoid crisis/meltdown noise
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        if btc_surge_long and sol_lagging_bull:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                horizon_seconds=1200,
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_ret": btc_ret,
                    "sol_recent_ret": sol_recent_ret,
                    "atr_bps": atr_bps,
                    "price": closes[-1]
                }
            )

        if btc_dump_short and sol_lagging_bear:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                horizon_seconds=1200,
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_ret": btc_ret,
                    "sol_recent_ret": sol_recent_ret,
                    "atr_bps": atr_bps,
                    "price": closes[-1]
                }
            )

        return None