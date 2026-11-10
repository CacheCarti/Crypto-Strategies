from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1500,  # ~5 bars (25 mins)
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.last_trade_bar = -999
        self.btc_window = 4
        self.min_btc_lead_diff = 0.55  # BTC delta exceeds SOL delta by 0.55%
        self.btc_ret_history = []
        self.entry_bar = -1

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 30:
            return None

        # Track BTC 24h return series to measure fresh impulse
        curr_btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append(curr_btc_ret)
        if len(self.btc_ret_history) > 30:
            self.btc_ret_history.pop(0)

        # Time-based exit for scalp (3 to 6 bars hold)
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if self.entry_bar > 0 and bars_held >= 5:
                direction = ctx.position_direction()
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "scalp_time_exit", "bars_held": bars_held})
            return None

        # Check cooldown after prior trade
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        if len(self.btc_ret_history) < self.btc_window + 1:
            return None

        # Measure fresh BTC impulse across the last N bars
        btc_impulse = curr_btc_ret - self.btc_ret_history[-self.btc_window]
        
        # Measure SOL return across the same window
        sol_ret = ((closes[-1] - closes[-self.btc_window]) / closes[-self.btc_window]) * 100.0

        # Dislocation between BTC impulse and SOL reaction
        lag_diff = btc_impulse - sol_ret

        # Micro trend filter (EMA 9 vs EMA 21 on SOL) to prevent trading into severe adverse local trends
        ema_fast = self._sma(closes[-9:], 9)
        ema_slow = self._sma(closes[-21:], 21)
        if ema_fast is None or ema_slow is None:
            return None

        # Trigger Long: Fresh sharp BTC burst, SOL lagging behind, not in deep crisis meltdown
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Long: BTC moved up sharply, SOL hasn't caught up, fast EMA above/near slow EMA
        if lag_diff >= self.min_btc_lead_diff and btc_impulse >= 0.70 and closes[-1] >= ema_slow * 0.998:
            self.last_trade_bar = ctx.bar_index
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.5 + min(lag_diff / 2.0, 0.35))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_catchup_long",
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_ret": round(sol_ret, 3),
                    "lag_diff": round(lag_diff, 3),
                    "close": closes[-1]
                }
            )

        # Short: BTC plunged sharply, SOL hasn't dropped yet, fast EMA below/near slow EMA
        if lag_diff <= -self.min_btc_lead_diff and btc_impulse <= -0.70 and closes[-1] <= ema_slow * 1.002:
            self.last_trade_bar = ctx.bar_index
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.5 + min(abs(lag_diff) / 2.0, 0.35))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_catchup_short",
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_ret": round(sol_ret, 3),
                    "lag_diff": round(lag_diff, 3),
                    "close": closes[-1]
                }
            )

        return None