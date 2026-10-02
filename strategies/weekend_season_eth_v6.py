from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class WeekendDriftSeasonality(Strategy):
    METADATA = {
        "name": "WeekendDriftSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 21
        self.ema_slow_period = 55
        self.rsi_period = 14
        self.last_entry_bar = -100
        self.last_exit_bar = -100
        self.cooldown_bars = 12
        self.last_traded_week = -1

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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_slow_period + 10)
        if len(closes) < self.ema_slow_period:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        current_week_id = iso_year * 100 + iso_week

        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        current_price = ctx.bar.close
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Active position management & scheduled exits
        if ctx.has_position():
            # Scheduled weekend exit: Sunday 20:00 UTC onwards or Monday early morning
            is_exit_window = (weekday == 6 and hour >= 20) or (weekday == 0 and hour <= 3)
            # Take profit acceleration if overbought during the weekend
            is_take_profit = rsi > 76.0 and (weekday == 5 or weekday == 6)

            if is_exit_window or is_take_profit:
                reason = "weekend_session_end" if is_exit_window else "weekend_rsi_overbought_tp"
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "price": current_price,
                        "bars_held": ctx.bar_index - self.last_entry_bar,
                    },
                )
            return None

        # Entry gating
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry window: Friday 17:00 UTC to 23:00 UTC
        is_friday_entry_window = (weekday == 4 and 17 <= hour <= 23)

        if is_friday_entry_window and self.last_traded_week != current_week_id:
            # Avoid entries during crisis regimes or extreme downtrends
            if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
                return None

            # Technical quality filters: not severely collapsing and not excessively overbought
            is_trend_support = current_price >= (ema_slow * 0.985)
            is_rsi_healthy = 38.0 <= rsi <= 72.0

            if is_trend_support and is_rsi_healthy:
                confidence = 0.65
                if current_price > ema_fast and rsi > 50.0:
                    confidence = 0.85

                self.last_entry_bar = ctx.bar_index
                self.last_traded_week = current_week_id

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=450.0,
                    take_profit_bps=750.0,
                    horizon_seconds=172800,
                    metadata={
                        "reason": "friday_weekend_seasonality_entry",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "ema_fast": round(ema_fast, 2),
                        "ema_slow": round(ema_slow, 2),
                        "price": current_price,
                        "regime": regime,
                    },
                )

        return None