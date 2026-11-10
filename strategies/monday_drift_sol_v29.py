from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.traded_this_window = False
        self.entry_bar = 0

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        var = sum((x - mean) ** 2 for x in slice_v) / period
        std = math.sqrt(var)
        if std == 0.0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour

        if weekday == 2 and hour == 0:
            self.traded_this_window = False

        if ctx.has_position():
            hold_bars = ctx.bar_index - self.entry_bar
            if weekday == 1 and hour >= 20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "monday_close_window", "hold_bars": hold_bars, "weekday": weekday, "hour": hour}
                )
            if weekday >= 2 and weekday <= 5:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "tuesday_hard_exit", "hold_bars": hold_bars, "weekday": weekday}
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if self.traded_this_window:
            return None

        is_sunday_night = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 6)
        if not (is_sunday_night or is_monday_early):
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50)
        if fear_greed < 20:
            return None

        z_48 = self._zscore(closes, 48)
        if z_48 is None or z_48 < -2.5 or z_48 > 1.8:
            return None

        sma_24 = self._sma(closes, 24)
        if sma_24 is None:
            return None

        current_close = ctx.bar.close
        dip_discount = (current_close - sma_24) / sma_24

        if dip_discount > 0.035:
            return None

        confidence = 0.65
        if z_48 < -0.5:
            confidence += 0.15
        if fear_greed > 45:
            confidence += 0.10
        confidence = min(1.0, max(0.1, confidence))

        self.traded_this_window = True
        self.entry_bar = ctx.bar_index

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "monday_drift_seasonality_entry",
                "weekday": weekday,
                "hour": hour,
                "z_48": round(z_48, 2),
                "fear_greed": fear_greed,
                "dip_discount_pct": round(dip_discount * 100, 2),
                "price": current_close
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index