from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 72000,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_until_bar = 0
        self.last_entry_week = -1
        self.bars_in_trade = 0

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14):
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
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        current_time = ctx.bar.timestamp
        weekday = current_time.weekday()
        hour = current_time.hour
        iso_year, iso_week, _ = current_time.isocalendar()

        if ctx.has_position():
            self.bars_in_trade += 1
            if weekday == 0 and hour >= 21:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "monday_window_close", "hour": hour, "weekday": weekday}
                )
            if weekday == 1:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "tuesday_hard_cutoff", "hour": hour, "weekday": weekday}
                )
            return None

        if ctx.bar_index < self.cooldown_until_bar:
            return None

        if self.last_entry_week == iso_week:
            return None

        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry boundary: Sunday 18:00 UTC through Monday 02:00 UTC
        is_entry_window = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 2)
        if not is_entry_window:
            return None

        rsi = self._rsi(closes, 14)
        ema_fast = self._ema(closes, 12)
        ema_slow = self._ema(closes, 26)
        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        price = ctx.bar.close
        fg_index = ctx.features.get("fear_greed_index", 50)

        # Avoid entries into severely deteriorating momentum or extreme overbought tops
        momentum_ok = price >= (ema_fast * 0.985)
        rsi_ok = 35.0 <= rsi <= 68.0

        if momentum_ok and rsi_ok:
            self.last_entry_week = iso_week
            confidence = 0.65
            if ema_fast > ema_slow:
                confidence += 0.10
            if fg_index < 40:
                confidence += 0.05
            confidence = min(0.9, max(0.5, confidence))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=320.0,
                take_profit_bps=520.0,
                horizon_seconds=72000,
                metadata={
                    "reason": "sunday_monday_seasonal_drift",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "fg_index": fg_index,
                }
            )

        return None