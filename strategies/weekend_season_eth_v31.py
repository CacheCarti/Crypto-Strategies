from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 129600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_until_bar = 0
        self.entry_bar = 0

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        current_bar = ctx.bar_index
        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour
        price = ctx.bar.close

        ema_trend = self._ema(closes, 30)
        rsi = self._rsi(closes, 14)

        if ema_trend is None or rsi is None:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        if ctx.has_position():
            bars_held = current_bar - self.entry_bar
            is_sunday_exit = (weekday == 6 and hour >= 20)
            is_monday_exit = (weekday == 0 and hour >= 4)
            rsi_overbought = rsi > 70.0
            trend_broken = price < (ema_trend * 0.985) and bars_held >= 6

            if is_sunday_exit or is_monday_exit or rsi_overbought or trend_broken:
                reason = "scheduled_weekend_exit"
                if rsi_overbought:
                    reason = "rsi_overbought_weekend_exit"
                elif trend_broken:
                    reason = "trend_broken_weekend_exit"
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": reason,
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                        "price": round(price, 2),
                        "ema30": round(ema_trend, 2),
                        "weekday": weekday,
                        "hour": hour,
                    }
                )
            return None

        if current_bar < self.cooldown_until_bar:
            return None

        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.45:
            return None

        # Entry window: Friday 16:00 to 23:00 UTC, or early Saturday 00:00 to 06:00 UTC
        is_friday_entry = (weekday == 4 and 16 <= hour <= 23)
        is_saturday_reentry = (weekday == 5 and 0 <= hour <= 6)

        if not (is_friday_entry or is_saturday_reentry):
            return None

        # Price and momentum filter to avoid entering into sharp selloffs
        if price < ema_trend * 0.995 or rsi < 36.0 or rsi > 68.0:
            return None

        confidence = 0.65
        if price > ema_trend:
            confidence += 0.1
        if 42.0 <= rsi <= 58.0:
            confidence += 0.1
        confidence = min(1.0, confidence)

        self.entry_bar = current_bar

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=320.0,
            take_profit_bps=480.0,
            horizon_seconds=129600,
            metadata={
                "reason": "friday_weekend_drift_entry",
                "rsi": round(rsi, 2),
                "ema30": round(ema_trend, 2),
                "price": round(price, 2),
                "weekday": weekday,
                "hour": hour,
                "regime": regime,
            }
        )