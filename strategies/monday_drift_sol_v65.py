from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 540.0,
        "declared_hold_seconds": 64800,  # ~18 hours hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -999
        self.cooldown_bars = 16
        self.entered_this_cycle = False
        self.last_seen_week = -1

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # 0=Monday, 6=Sunday
        hour = ts.hour
        iso_week = ts.isocalendar()[1]

        # Reset weekly entry flag at the start of a new week cycle
        if iso_week != self.last_seen_week:
            self.entered_this_cycle = False
            self.last_seen_week = iso_week

        # Manage open position
        if ctx.has_position():
            # Mandatory seasonal time-based exit: Monday 20:00-23:00 UTC before Tuesday rollover
            if weekday == 0 and hour >= 21:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "monday_us_close_exit", "hour": hour, "weekday": weekday}
                )
            # If position lingers into Tuesday or later, exit immediately
            if weekday >= 1 and weekday < 5:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "tuesday_post_drift_exit", "hour": hour, "weekday": weekday}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # One setup per weekly seasonality window
        if self.entered_this_cycle:
            return None

        # Entry window: Sunday late UTC (18:00 - 23:00) or very early Monday (00:00 - 03:00)
        is_sunday_night = (weekday == 6 and hour >= 18)
        is_early_monday = (weekday == 0 and hour <= 3)

        if not (is_sunday_night or is_early_monday):
            return None

        # Market regime filter: reject crisis / meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.45:
            return None

        # Technical indicators
        rsi14 = self._rsi(closes, 14)
        sma20 = self._sma(closes, 20)
        sma50 = self._sma(closes, 50)

        if rsi14 is None or sma20 is None or sma50 is None:
            return None

        current_price = closes[-1]

        # High-conviction entry conditions:
        # Avoid severely overbought weekend pumps (RSI < 66)
        # Avoid freefalling dumps where weekend crash is accelerating (RSI > 32)
        # Prefer price holding reasonably near or above SMA50 to avoid strong structural bear trends
        if not (32.0 <= rsi14 <= 66.0):
            return None

        if current_price < sma50 * 0.965:
            return None

        # Modulate confidence based on sentiment & trend alignment
        fgi = ctx.features.get("fear_greed_index", 50.0)
        confidence = 0.65
        if fgi > 40:
            confidence += 0.10
        if current_price > sma20:
            confidence += 0.10
        confidence = min(0.90, confidence)

        self.entered_this_cycle = True
        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sunday_to_monday_drift_entry",
                "weekday": weekday,
                "hour": hour,
                "rsi14": round(rsi14, 2),
                "fgi": fgi,
                "sma20": round(sma20, 2),
                "sma50": round(sma50, 2),
                "price": current_price,
            }
        )