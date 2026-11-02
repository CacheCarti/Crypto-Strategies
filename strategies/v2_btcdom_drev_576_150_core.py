from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DRev2(Strategy):
    METADATA = {
        "name": "DRev2 btcdom 576/1.5/core",
        "domain": "btcdom_usdt",
        "declared_sl_bps": 240.0,
        "declared_tp_bps": 300.0,
        "declared_hold_seconds": 3600 * 20,
        "warmup_bars": 581,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 576
        self.k = 1.50
        self.cooldown_bars = 24
        self.target_frac = 0.00
        self.vol_gate = False
        self.side = 'both'
        self.sess = False
        self.sess_start = 0
        self.sess_end = 24
        self.entry_cum_z = None
        self.last_exit_bar = -999

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_cum_z = None


    def _ms(self, vals):
        n = len(vals)
        m = sum(vals) / n
        return m, math.sqrt(sum((x - m) ** 2 for x in vals) / n)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 2:
            return None
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        m, s = self._ms(rets)
        if s <= 1e-10:
            return None
        cum = math.log(closes[-1] / closes[0])
        cum_z = cum / (s * math.sqrt(len(rets)))
        hour = ctx.bar.timestamp.hour
        in_session = (not self.sess) or (self.sess_start <= hour < self.sess_end)

        if ctx.has_position():
            d = ctx.position_direction()
            # partial-reversion target: bank at target_frac of the entry stretch
            if self.entry_cum_z is not None:
                if d == "long" and cum_z >= self.target_frac * self.entry_cum_z:
                    return ctx.signal("flat", 0.75, metadata={"reason": "partial_target"})
                if d == "short" and cum_z <= self.target_frac * self.entry_cum_z:
                    return ctx.signal("flat", 0.75, metadata={"reason": "partial_target"})
            if (d == "long" and cum_z >= 0) or (d == "short" and cum_z <= 0):
                return ctx.signal("flat", 0.75, metadata={"reason": "reversion_done"})
            return None

        if not in_session:
            return None
        if self.vol_gate:
            bars = [abs(closes[-i] / closes[-i - 1] - 1.0) for i in range(1, 289)]
            bars_sorted = sorted(bars)
            p75 = bars_sorted[int(len(bars_sorted) * 0.75)]
            if abs(closes[-1] / closes[-2] - 1.0) > p75:
                return None
        if cum_z < -self.k and self.side in ("both", "long"):
            self.entry_cum_z = cum_z
            return ctx.signal("long", min(0.85, 0.6 + (-cum_z - self.k) * 0.15),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={"reason": "dump_fade", "cum_z": round(cum_z, 3)})
        if cum_z > self.k and self.side in ("both", "short"):
            self.entry_cum_z = cum_z
            return ctx.signal("short", min(0.85, 0.6 + (cum_z - self.k) * 0.15),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={"reason": "pump_fade", "cum_z": round(cum_z, 3)})
        return None
