from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcBetaCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcBetaCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 85.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_bars = 35
        self.entry_bar = -1
        self.last_exit_bar = -100
        self.cooldown_bars = 12
        self.max_hold_bars = 5
        self.btc_window = 3
        self.btc_ret_history = []

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Track btc_return_pct internally to compute delta across 3 bars (15 mins)
        current_btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append(current_btc_ret)
        if len(self.btc_ret_history) > 20:
            self.btc_ret_history.pop(0)

        # Time-based exit for scalp
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            if bars_in_trade >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_hold_time_reached", "bars_held": bars_in_trade}
                )
            return None

        # Mandatory cooldown after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Need sufficient feature history
        if len(self.btc_ret_history) <= self.btc_window:
            return None

        # Calculate impulse of BTC over last ~15 mins
        btc_impulse = current_btc_ret - self.btc_ret_history[-self.btc_window - 1]
        
        # Calculate SOL return over same period
        sol_ret_window = (closes[-1] - closes[-self.btc_window - 1]) / closes[-self.btc_window - 1] * 100.0

        # Regime and market stress safety
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Filter out flat volatility to avoid false breakouts
        vol_window = 14
        sd = self._stddev(closes, vol_window)
        if sd is None or sd / closes[-1] < 0.0010:
            return None

        # Long: BTC moved sharply up (> 1.25%), but SOL has lagged substantially (< 0.40%)
        if btc_impulse > 1.25 and sol_ret_window < 0.40:
            gap = btc_impulse - sol_ret_window
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(0.9, 0.6 + (gap * 0.1)),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_beta_catchup_long",
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_ret": round(sol_ret_window, 3),
                    "gap": round(gap, 3)
                }
            )

        # Short: BTC moved sharply down (< -1.25%), but SOL has lagged substantially (> -0.40%)
        if btc_impulse < -1.25 and sol_ret_window > -0.40:
            gap = abs(btc_impulse) - abs(sol_ret_window)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=min(0.9, 0.6 + (gap * 0.1)),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_beta_catchup_short",
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_ret": round(sol_ret_window, 3),
                    "gap": round(gap, 3)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index