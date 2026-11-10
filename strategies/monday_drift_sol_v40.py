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
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_week: int = -1
        self.cooldown_until_bar: int = 0
        self.bars_in_position: int = 0

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 12
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        current_week_id = iso_year * 100 + iso_week

        ema24 = self._ema(closes, 24)
        rsi14 = self._rsi(closes, 14)
        if ema24 is None or rsi14 is None:
            return None

        has_pos = ctx.has_position()

        if has_pos:
            self.bars_in_position += 1
            # Exit window: Monday 20:00 UTC through Tuesday 02:00 UTC, or held > 24 bars
            exit_time = (weekday == 0 and hour >= 20) or (weekday == 1 and hour < 3)
            if exit_time or self.bars_in_position >= 24:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "monday_drift_window_complete",
                        "weekday": weekday,
                        "hour": hour,
                        "bars_held": self.bars_in_position,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Flat state - check entry gating
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        if self.last_traded_week == current_week_id:
            return None

        # Entry boundary: Sunday 18:00 to 23:00 UTC
        in_entry_window = (weekday == 6 and hour >= 18)
        if not in_entry_window:
            return None

        # Technical filter: Price must show short-term stability (close above EMA24) and not be overbought
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        if ctx.bar.close > ema24 and 40.0 <= rsi14 <= 68.0:
            self.last_traded_week = current_week_id
            self.bars_in_position = 0
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=320.0,
                take_profit_bps=520.0,
                horizon_seconds=72000,
                metadata={
                    "reason": "sunday_late_drift_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi14, 2),
                    "ema24": round(ema24, 2),
                    "close": ctx.bar.close,
                    "regime": regime
                }
            )

        return None