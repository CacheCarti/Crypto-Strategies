from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_week: Optional[int] = None
        self.cooldown_until_bar: int = 0
        self.in_event: bool = False

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 12
        self.in_event = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        current_week_key = (iso_year, iso_week)

        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Check exit boundary for open position
        if ctx.has_position():
            # Exit by late Monday (22:00 UTC) or anytime Tuesday/Wednesday
            if weekday == 0 and hour >= 22:
                self.in_event = False
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "monday_window_close_event_boundary", "hour": hour, "weekday": weekday}
                )
            if weekday == 1:
                self.in_event = False
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "tuesday_hard_cutoff", "hour": hour, "weekday": weekday}
                )
            return None

        # Hard cooldown check
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Once per weekly event cycle guard
        if self.last_traded_week == current_week_key:
            return None

        # Invalidation against severe macro stress
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.45:
            return None

        # Entry window: Sunday 18:00 UTC to 23:00 UTC
        is_entry_window = (weekday == 6 and hour >= 18)
        if not is_entry_window:
            return None

        # Technical validation: not exhausted overbought, mild trend alignment
        rsi = self._rsi(closes, period=14)
        sma24 = self._sma(closes, period=24)
        if rsi is None or sma24 is None:
            return None

        # Avoid buying blow-off tops right before close
        if rsi > 68.0:
            return None

        # Trend and momentum filter: price should not be heavily decoupled downwards
        price = ctx.bar.close
        if price < sma24 * 0.93:
            return None

        # Higher confidence when rsi indicates attractive entry condition
        confidence = 0.65
        if 35.0 <= rsi <= 55.0:
            confidence = 0.85

        self.last_traded_week = current_week_key
        self.in_event = True

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=400.0,
            take_profit_bps=650.0,
            horizon_seconds=86400,
            metadata={
                "reason": "sunday_into_monday_drift_entry",
                "weekday": weekday,
                "hour": hour,
                "rsi": round(rsi, 2),
                "sma24": round(sma24, 2),
                "price": round(price, 2),
                "crisis_score": round(crisis_score, 2),
            }
        )