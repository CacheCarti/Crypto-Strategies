from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondayDriftSeasonality(Strategy):
    METADATA = {
        "name": "SolMondayDriftSeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 72000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.trades_this_week = 0
        self.current_week = -1
        self.cooldown_bars = 12

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
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()

        if iso_week != self.current_week:
            self.current_week = iso_week
            self.trades_this_week = 0

        # Exit conditions when holding position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Target exit: Monday late US session (20:00 to 23:00 UTC) or Tuesday 00:00+
            time_to_exit = (weekday == 0 and hour >= 21) or (weekday == 1)
            max_hold_reached = bars_held >= 26

            if time_to_exit or max_hold_reached:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "monday_drift_window_complete",
                        "weekday": weekday,
                        "hour": hour,
                        "bars_held": bars_held,
                        "price": ctx.bar.close,
                    },
                )
            return None

        # Entry logic: 1 trade max per week, mandatory cooldown
        if self.trades_this_week >= 1:
            return None
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Crisp entry window: Sunday 20:00 UTC to Monday 04:00 UTC
        is_sunday_entry = (weekday == 6 and hour >= 20)
        is_early_monday_entry = (weekday == 0 and hour <= 4)
        if not (is_sunday_entry or is_early_monday_entry):
            return None

        # Stricter price confirmation filters to beat baseline drift
        ema24 = self._ema(closes, 24)
        rsi14 = self._rsi(closes, 14)
        if ema24 is None or rsi14 is None:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Must not be severely overbought into the weekly reset, but exhibiting constructive support
        price = ctx.bar.close
        if rsi14 < 62.0 and price >= (ema24 * 0.985):
            self.entry_bar = ctx.bar_index
            self.trades_this_week += 1
            confidence = 0.75 if price >= ema24 else 0.60
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=750.0,
                horizon_seconds=72000,
                metadata={
                    "reason": "sunday_monday_drift_structural_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi14, 2),
                    "ema24": round(ema24, 2),
                    "price": price,
                    "regime": regime,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999