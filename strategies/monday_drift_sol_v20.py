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
        self.last_traded_week: Optional[int] = None
        self.cooldown_until_bar: int = 0
        self.entry_bar_idx: Optional[int] = None

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

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
        self.cooldown_until_bar = ctx.bar_index + 12
        self.entry_bar_idx = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        cal_week = ts.isocalendar()[1]

        # Manage active position
        if ctx.has_position():
            # Exit near Monday close / Tuesday start (Monday 22:00-23:00 UTC or Tuesday onward)
            is_exit_window = (weekday == 0 and hour >= 22) or (weekday == 1)
            bars_held = (ctx.bar_index - self.entry_bar_idx) if self.entry_bar_idx is not None else 20
            
            if is_exit_window or bars_held >= 26:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "monday_window_completed",
                        "bars_held": bars_held,
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Entry guards
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Trade exactly once per calendar week to prevent multi-entry churn
        if self.last_traded_week == cal_week:
            return None

        # Crisp entry window: Sunday 20:00 UTC to Monday 04:00 UTC
        is_sunday_evening = (weekday == 6 and hour >= 20)
        is_monday_early = (weekday == 0 and hour <= 4)
        if not (is_sunday_evening or is_monday_early):
            return None

        # Crisis / Meltdown filter via market context
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Price validation: Avoid buying severely crashing markets (RSI floor and short-term trend)
        rsi_14 = self._rsi(closes, 14)
        ema_short = self._ema(closes, 9)
        ema_trend = self._ema(closes, 30)

        if rsi_14 is None or ema_short is None or ema_trend is None:
            return None

        # Stricter event boundary: Require RSI not completely melted (> 32) and not heavily overbought (< 68)
        # and short-term structure stabilizing (close above 9 EMA or 9 EMA > 30 EMA)
        if rsi_14 < 32.0 or rsi_14 > 68.0:
            return None

        stabilizing = (ctx.bar.close >= ema_short) or (ema_short >= ema_trend)
        if not stabilizing:
            return None

        self.last_traded_week = cal_week
        self.entry_bar_idx = ctx.bar_index

        conf = 0.75 if (ctx.bar.close >= ema_trend and rsi_14 >= 45.0) else 0.65

        return ctx.signal(
            "long",
            confidence=conf,
            stop_loss_bps=320.0,
            take_profit_bps=520.0,
            horizon_seconds=72000,
            metadata={
                "reason": "sunday_monday_seasonality_long",
                "weekday": weekday,
                "hour": hour,
                "rsi_14": round(rsi_14, 2),
                "ema9": round(ema_short, 2),
                "ema30": round(ema_trend, 2),
                "price": round(ctx.bar.close, 2)
            }
        )