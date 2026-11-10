from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 30
        self.last_exit_bar = -100
        self.cooldown_bars = 12
        self.entry_bar = -1
        self.has_entered_this_week = False
        self.last_week_number = -1

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup + 1)
        if len(closes) < self.warmup + 1:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()

        if iso_week != self.last_week_number:
            self.last_week_number = iso_week
            self.has_entered_this_week = False

        rsi_val = self._rsi(closes, period=14)
        ema20 = self._ema(closes, period=20)
        curr_price = ctx.bar.close

        if ctx.has_position():
            # Exit logic: hold through Monday US hours, exit by Tuesday 00:00 UTC or if trend collapses
            should_exit_calendar = (weekday == 0 and hour >= 22) or (weekday == 1)
            
            # Momentum breakdown safety exit
            should_exit_technical = (rsi_val is not None and rsi_val < 38.0 and ctx.bar_index - self.entry_bar >= 4)

            if should_exit_calendar or should_exit_technical:
                reason = "monday_session_end" if should_exit_calendar else "momentum_failure"
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi_val, 2) if rsi_val else 0.0,
                        "price": curr_price
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # One entry setup per calendar week to prevent overtrading and lucky cluster reliance
        if self.has_entered_this_week:
            return None

        # Market regime filter from engine context
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Seasonal Entry Window: Sunday late UTC (18:00 - 23:00) OR early Monday Asian session (00:00 - 05:00)
        is_sunday_window = (weekday == 6 and hour >= 18)
        is_monday_window = (weekday == 0 and hour <= 5)

        if not (is_sunday_window or is_monday_window):
            return None

        # Technical confirmation: avoid buying freefalls; ensure oversold stabilization or price above short EMA
        rsi_ok = rsi_val is not None and (35.0 <= rsi_val <= 65.0)
        ema_ok = ema20 is not None and (curr_price >= ema20 * 0.985)

        if rsi_ok and ema_ok:
            self.has_entered_this_week = True
            self.entry_bar = ctx.bar_index

            conf = 0.70
            if rsi_val > 48.0:
                conf += 0.10

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=320.0,
                take_profit_bps=480.0,
                horizon_seconds=86400,
                metadata={
                    "reason": "sunday_monday_seasonality_long",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi_val, 2),
                    "ema20": round(ema20, 2),
                    "price": curr_price
                }
            )

        return None