from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 16
        self.traded_this_week = -1

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        iso_week = ts.isocalendar()[1]

        # Manage open position
        if ctx.has_position():
            # Exit late Monday / early Tuesday (Monday 22:00 UTC through Tuesday 02:00 UTC)
            if (weekday == 0 and hour >= 22) or (weekday == 1 and hour >= 0):
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "monday_session_complete", "weekday": weekday, "hour": hour}
                )
            return None

        # Check cooldown and one-trade-per-week throttle
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if self.traded_this_week == iso_week:
            return None

        # Entry window: Sunday late UTC (18:00 - 23:00) into Monday early morning (00:00 - 05:00)
        valid_entry_time = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 5)
        if not valid_entry_time:
            return None

        # Technical confirmation: avoid buying deep falling knives, confirm momentum isn't completely washed out
        ema24 = self._ema(closes, 24)
        rsi = self._rsi(closes, 14)
        if ema24 is None or rsi is None:
            return None

        # Regime & momentum filter:
        # RSI must be recovering from weekend weakness (RSI > 38 and not severely overbought < 68)
        # Price must not be trailing more than 3.5% below EMA24 (prevents entering severe crisis breakdowns)
        price = ctx.bar.close
        price_to_ema_ratio = price / ema24

        if 38.0 <= rsi <= 68.0 and price_to_ema_ratio >= 0.965:
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            self.traded_this_week = iso_week
            confidence = 0.65 if rsi < 55.0 else 0.75

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_recovery_drift_long",
                    "rsi": round(rsi, 2),
                    "ema24": round(ema24, 2),
                    "price": round(price, 2),
                    "weekday": weekday,
                    "hour": hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index