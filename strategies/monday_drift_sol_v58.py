from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.traded_this_window = False
        self.last_seen_week = -1

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1.0 - k)
        return val

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
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()

        if iso_week != self.last_seen_week:
            self.traded_this_window = False
            self.last_seen_week = iso_week

        ema24 = self._ema(closes, 24)
        rsi14 = self._rsi(closes, 14)
        if ema24 is None or rsi14 is None:
            return None

        current_price = ctx.bar.close

        # Exit management if position is open
        if ctx.has_position():
            # Exit scheduled on Monday late US hours (22:00 UTC) or Tuesday morning
            exit_by_time = (weekday == 0 and hour >= 22) or (weekday == 1 and hour >= 1)
            # Early defensive cut if RSI breaks down completely
            rsi_breakdown = rsi14 < 32.0

            if exit_by_time or rsi_breakdown:
                reason = "monday_window_expired" if exit_by_time else "defensive_rsi_breakdown"
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi14, 2),
                        "price": round(current_price, 2),
                    },
                )
            return None

        # Entry gate logic
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if self.traded_this_window:
            return None

        # Market regime filter: avoid entering during panic meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Target entry window: Sunday 18:00 UTC through Monday 04:00 UTC
        is_sunday_entry = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 4)
        in_entry_window = is_sunday_entry or is_monday_early

        if not in_entry_window:
            return None

        # Price-based confirmation: trend stabilization & not heavily overbought
        is_stabilizing = current_price >= ema24 * 0.99
        is_not_overbought = rsi14 < 68.0

        if is_stabilizing and is_not_overbought:
            self.traded_this_window = True
            self.entry_bar = ctx.bar_index

            confidence = 0.75 if current_price > ema24 else 0.60
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "monday_drift_seasonality_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi14, 2),
                    "ema24": round(ema24, 2),
                    "price": round(current_price, 2),
                },
            )

        return None