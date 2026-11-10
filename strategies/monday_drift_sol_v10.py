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
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_week = -1
        self.cooldown_until_bar = 0

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour
        cal_week = dt.isocalendar()[1]

        # Manage open position
        if ctx.has_position():
            # Exit scheduled on Monday evening UTC (hour >= 20) or Tuesday early
            if (weekday == 0 and hour >= 20) or weekday == 1:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "monday_session_end_exit",
                    "weekday": weekday,
                    "hour": hour,
                    "close": ctx.bar.close
                })
            return None

        # Cooldown guard
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Entry boundary: Sunday 18:00 UTC to Monday 04:00 UTC
        is_entry_window = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 4)
        if not is_entry_window:
            return None

        # Exactly one trade execution per calendar week
        if self.last_traded_week == cal_week:
            return None

        # Avoid market crisis meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Momentum & oversold bounce trigger
        ema20 = self._ema(closes, 20)
        rsi14 = self._rsi(closes, 14)
        if ema20 is None or rsi14 is None:
            return None

        # Clean event condition: Sunday weekend stabilization
        # Not severely overbought, and not in freefall
        if 32.0 <= rsi14 <= 62.0 and ctx.bar.close >= closes[-2]:
            self.last_traded_week = cal_week
            self.cooldown_until_bar = ctx.bar_index + 12
            
            confidence = 0.75 if ctx.bar.close > ema20 else 0.65
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=320.0,
                take_profit_bps=520.0,
                metadata={
                    "reason": "sunday_into_monday_drift",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi14, 2),
                    "ema20": round(ema20, 2),
                    "close": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 8