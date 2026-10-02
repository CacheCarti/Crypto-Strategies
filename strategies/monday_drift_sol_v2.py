from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolWeekdaySeasonality(Strategy):
    METADATA = {
        "name": "SolWeekdaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 700.0,
        "declared_hold_seconds": 72000,
        "warmup_bars": 55,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_fast_period = 12
        self.ema_slow_period = 48
        self.cooldown_bars = 10
        self.last_exit_bar = -100
        self.entry_bar = -100

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

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period
        for v in values[period:]:
            ema_val = v * k + ema_val * (1.0 - k)
        return ema_val

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(60)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour

        rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        fg_index = ctx.features.get("fear_greed_index", 50)

        # Handle Open Positions (Exit Management)
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar

            # 1. Seasonality Target Reached: Monday late night UTC / Tuesday early hours
            monday_exit = (weekday == 0 and hour >= 22) or (weekday == 1 and hour >= 1)
            # 2. Indicator Take-Profit Trigger
            overbought_exit = rsi >= 72.0 and bars_held >= 4
            # 3. Maximum Hold Time Exceeded (~28 bars on 1h timeframe)
            timeout_exit = bars_held >= 28

            if monday_exit or overbought_exit or timeout_exit:
                self.last_exit_bar = ctx.bar_index
                reason = "monday_us_session_close" if monday_exit else ("rsi_overbought_exit" if overbought_exit else "max_hold_time_exit")
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "bars_held": bars_held,
                        "price": current_price,
                    },
                )
            return None

        # Cooldown guard: prevent re-entering too soon
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Crisis filter: skip entries during extreme market panic
        if crisis_score > 0.70 or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Primary Entry Setup: Sunday Late UTC Seasonality Buy
        # Sunday (weekday 6) starting 18:00 UTC through 23:00 UTC
        is_sunday_window = (weekday == 6 and hour >= 18)
        sunday_filter = rsi < 62.0 and current_price >= ema_slow * 0.94

        if is_sunday_window and sunday_filter:
            confidence = 0.75 if rsi < 45.0 else 0.65
            if fg_index < 30:
                confidence = min(1.0, confidence + 0.1)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_late_seasonality_long",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "fg_index": fg_index,
                    "price": current_price,
                },
            )

        # Secondary Entry Setup: Mid-Week Rebound (Wed/Thu oversold pullbacks)
        is_midweek_window = (weekday in (2, 3) and 8 <= hour <= 20)
        midweek_dip = rsi < 36.0 and current_price > ema_fast * 0.98

        if is_midweek_window and midweek_dip:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.70,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "midweek_rebound_dip_buy",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": current_price,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index