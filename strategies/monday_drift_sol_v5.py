from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolWeekendReboundSwing(Strategy):
    METADATA = {
        "name": "SolWeekendReboundSwing",
        "domain": "sol_usdc",
        "declared_sl_bps": 360.0,
        "declared_tp_bps": 680.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 10
        self.rsi_period = 14
        self.ema_fast_period = 12
        self.ema_slow_period = 26

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Sunday=6
        hour = ts.hour
        price = ctx.bar.close

        rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # 1. Manage Active Positions
        if ctx.has_position():
            # Seasonality time exit: Monday late night (hour >= 22) or early Tuesday
            is_monday_close = (weekday == 0 and hour >= 22)
            is_tuesday_morning = (weekday == 1 and hour >= 2)
            is_overbought_peak = rsi is not None and rsi > 78.0

            if is_monday_close or is_tuesday_morning or is_overbought_peak:
                exit_reason = (
                    "rsi_blowoff_exit"
                    if is_overbought_peak
                    else "monday_session_end"
                )
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": exit_reason,
                        "rsi": rsi,
                        "weekday": weekday,
                        "hour": hour,
                        "price": price,
                    },
                )
            return None

        # 2. Cooldown Guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if crisis_score > 0.65 or rsi is None or ema_fast is None or ema_slow is None:
            return None

        # 3. Setup A: Sunday Evening / Early Monday Seasonality Long
        # Window: Sunday 18:00 UTC through Monday 04:00 UTC
        is_sunday_evening = (weekday == 6 and hour >= 18)
        is_monday_dawn = (weekday == 0 and hour <= 4)
        is_seasonality_window = is_sunday_evening or is_monday_dawn

        if is_seasonality_window and rsi < 62.0:
            confidence = 0.70
            if rsi < 40.0:
                confidence += 0.15
            if ema_fast > ema_slow:
                confidence += 0.10

            return ctx.signal(
                "long",
                confidence=min(confidence, 0.95),
                stop_loss_bps=360.0,
                take_profit_bps=680.0,
                horizon_seconds=86400,
                metadata={
                    "reason": "sunday_monday_seasonality_long",
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "weekday": weekday,
                    "hour": hour,
                    "price": price,
                    "crisis_score": crisis_score,
                },
            )

        # 4. Setup B: Mid-week Dip Recovery (Wednesday / Thursday Oversold Bounce)
        is_midweek = weekday in (2, 3)
        if is_midweek and rsi < 32.0 and price >= closes[-2]:
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=300.0,
                take_profit_bps=550.0,
                horizon_seconds=57600,
                metadata={
                    "reason": "midweek_oversold_reversal",
                    "rsi": round(rsi, 2),
                    "weekday": weekday,
                    "hour": hour,
                    "price": price,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index