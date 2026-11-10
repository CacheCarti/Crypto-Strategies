from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcBetaCatchup(Strategy):
    METADATA = {
        "name": "SolBtcBetaCatchup",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1500,  # 3-5 5m bars
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.max_hold_bars = 5
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.prev_btc_ret = None
        self.lookback_bars = 4

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -100

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        # Manage open position exit via horizon or time-based decay
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1
            if bars_in_trade >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_exhaustion", "bars_held": bars_in_trade}
                )
            return None

        # Cooldown guard: enforce selective entry pacing
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate: Prior evidence shows lead-lag fails during volatile/crisis chop
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        if self.prev_btc_ret is None:
            self.prev_btc_ret = btc_ret
            return None

        # Measure freshness: BTC 24h return jump over the last lookback window
        # Sharp impulse requires BTC 24h return to change significantly recently
        btc_impulse = btc_ret - self.prev_btc_ret
        self.prev_btc_ret = btc_ret

        # Calculate SOL's recent price return over the same lag window (last 4 bars = 20 mins)
        sol_recent_ret = (closes[-1] - closes[-self.lookback_bars]) / closes[-self.lookback_bars] * 100.0

        # BTC overall 24h trend magnitude threshold (> 1.5%)
        btc_mag = abs(btc_ret)
        if btc_mag < 1.5:
            return None

        # Volatility check: calculate ATR-like metric for dynamic SL/TP
        highs = ctx.highs(14)
        lows = ctx.lows(14)
        trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(1, 14)]
        atr = sum(trs) / len(trs)
        atr_bps = (atr / closes[-1]) * 10000.0
        
        sl_bps = max(60.0, min(130.0, atr_bps * 1.2))
        tp_bps = max(110.0, min(240.0, atr_bps * 2.2))

        # Condition 1: BTC Bullish Impulse, SOL has lagged (SOL return is muted relative to impulse)
        if btc_ret >= 1.5 and btc_impulse >= 0.25 and sol_recent_ret < 0.35:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_sol_lag_bull",
                    "btc_ret": round(btc_ret, 3),
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_recent_ret": round(sol_recent_ret, 3),
                    "atr_bps": round(atr_bps, 1),
                }
            )

        # Condition 2: BTC Bearish Impulse, SOL has lagged
        if btc_ret <= -1.5 and btc_impulse <= -0.25 and sol_recent_ret > -0.35:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_sol_lag_bear",
                    "btc_ret": round(btc_ret, 3),
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_recent_ret": round(sol_recent_ret, 3),
                    "atr_bps": round(atr_bps, 1),
                }
            )

        return None