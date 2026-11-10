from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 500.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 172800,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 20
        self.slow_ema_period = 50
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entered_this_weekend = False
        self.last_seen_weekend_id = -1

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + 5)
        if len(closes) < self.slow_ema_period + 5:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        current_weekend_id = iso_year * 100 + iso_week

        if current_weekend_id != self.last_seen_weekend_id:
            self.last_seen_weekend_id = current_weekend_id
            self.entered_this_weekend = False

        if ctx.has_position():
            if weekday == 6 and hour >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "sunday_evening_exit",
                        "price": ctx.bar.close,
                        "weekday": weekday,
                        "hour": hour,
                    }
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        is_friday_entry_window = (weekday == 4 and 18 <= hour <= 23)
        if not is_friday_entry_window or self.entered_this_weekend:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime == "bear":
            return None

        ema_fast = self._ema(closes, self.fast_ema_period)
        ema_slow = self._ema(closes, self.slow_ema_period)
        if ema_fast is None or ema_slow is None:
            return None

        price = ctx.bar.close
        if price < ema_slow * 0.985:
            return None

        confidence = 0.70
        if ema_fast > ema_slow:
            confidence += 0.15
        if trend_regime == "bull":
            confidence += 0.10
        confidence = min(1.0, confidence)

        self.entered_this_weekend = True

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "friday_evening_weekend_drift",
                "price": price,
                "ema_fast": round(ema_fast, 2),
                "ema_slow": round(ema_slow, 2),
                "trend_regime": trend_regime,
                "crisis_score": round(crisis_score, 3),
                "weekday": weekday,
                "hour": hour,
            }
        )