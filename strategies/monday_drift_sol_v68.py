from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.cooldown_bars = 16
        self.last_traded_week = -1

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        current_time = ctx.bar.timestamp
        weekday = current_time.weekday()
        hour = current_time.hour
        iso_year, iso_week, _ = current_time.isocalendar()
        current_week_id = iso_year * 100 + iso_week

        # Check existing position for exit logic
        if ctx.has_position():
            # Event boundary exit: Monday late session (>= 21:00 UTC) or Tuesday onward
            is_monday_close = (weekday == 0 and hour >= 21)
            is_past_monday = (weekday > 0 and weekday < 6)
            bars_held = ctx.bar_index - self.entry_bar

            if is_monday_close or is_past_monday or bars_held >= 26:
                rsi_val = self._rsi(closes, 14) or 50.0
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "monday_event_window_end",
                        "weekday": weekday,
                        "hour": hour,
                        "bars_held": bars_held,
                        "rsi": round(rsi_val, 2),
                    }
                )
            return None

        # Check cooldown to prevent excessive entries
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Restrict to strictly one planned cycle per calendar week
        if current_week_id == self.last_traded_week:
            return None

        # Market regime filter: avoid executing into market meltdowns
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Event Entry Window: Sunday 18:00 UTC through Sunday 23:00 UTC
        is_sunday_window = (weekday == 6 and hour >= 18)
        if not is_sunday_window:
            return None

        # Price-based confirmation: avoid catching parabolic falling knives
        sma20 = self._sma(closes, 20)
        rsi14 = self._rsi(closes, 14)
        if sma20 is None or rsi14 is None:
            return None

        # Avoid extreme momentum breakdown while ensuring room for drift recovery
        if rsi14 < 28.0 or rsi14 > 68.0:
            return None

        price = ctx.bar.close
        # Price shouldn't be deep in a structural crash relative to recent 20h trend
        if price < sma20 * 0.94:
            return None

        self.last_traded_week = current_week_id
        self.entry_bar = ctx.bar_index

        confidence = 0.65
        if rsi14 < 45.0:
            confidence += 0.15
        if price >= sma20:
            confidence += 0.10

        return ctx.signal(
            "long",
            confidence=min(1.0, confidence),
            stop_loss_bps=320.0,
            take_profit_bps=480.0,
            horizon_seconds=86400,
            metadata={
                "reason": "sunday_to_monday_drift_entry",
                "weekday": weekday,
                "hour": hour,
                "price": round(price, 4),
                "rsi14": round(rsi14, 2),
                "sma20": round(sma20, 4),
                "regime": market_regime,
            }
        )