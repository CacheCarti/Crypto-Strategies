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
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 16
        self.ema_fast_len = 12
        self.ema_slow_len = 48
        self.rsi_len = 14

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(60)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        price = ctx.bar.close

        ema_fast = self._ema(closes, self.ema_fast_len)
        ema_slow = self._ema(closes, self.ema_slow_len)
        rsi = self._rsi(closes, self.rsi_len)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        # Manage existing position: hold Sunday night through Monday US close
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mandatory exit Tuesday 00:00 UTC or later, or Monday late evening (23:00 UTC)
            is_exit_time = (weekday == 0 and hour >= 23) or (weekday == 1)
            # Technical failure exit: early exit if structure breaks severely
            trend_broken = price < ema_slow * 0.975

            if is_exit_time or trend_broken:
                self.last_exit_bar = ctx.bar_index
                reason = "scheduled_tuesday_turnaround" if is_exit_time else "trend_invalidation"
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": reason, "weekday": weekday, "hour": hour, "rsi": rsi, "price": price}
                )
            return None

        # Check cooldown to prevent rapid churn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Window: Sunday 18:00 UTC through Monday 08:00 UTC
        is_sunday_evening = (weekday == 6 and hour >= 18)
        is_monday_morning = (weekday == 0 and hour <= 8)
        seasonality_active = is_sunday_evening or is_monday_morning

        if not seasonality_active:
            return None

        # Regime & momentum filter: avoid buying into an aggressive crash
        # Require RSI not extremely overbought and price stabilizing near or above fast EMA
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Dynamic state check: trend slope or pullback stabilization
        not_overbought = rsi < 68.0
        stabilizing = price >= (ema_fast * 0.992)

        if not_overbought and stabilizing:
            confidence = 0.70
            if price > ema_slow:
                confidence = 0.85

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_monday_drift_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": price,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index