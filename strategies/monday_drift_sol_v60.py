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
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 12
        self.entry_week_id = -1

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        current_week_id = iso_year * 100 + iso_week

        ema_fast = self._ema(closes, 12)
        ema_slow = self._ema(closes, 26)
        rsi = self._rsi(closes, 14)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        has_pos = ctx.has_position()

        # Exit logic: Tuesday at or after 00:00 UTC, or late Monday past 21:00 UTC
        if has_pos:
            is_exit_window = (weekday == 0 and hour >= 21) or (weekday == 1 and hour >= 0) or (weekday > 1 and weekday < 6)
            if is_exit_window:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "seasonal_target_window_closed",
                        "weekday": weekday,
                        "hour": hour,
                        "price": ctx.bar.close,
                        "rsi": round(rsi, 2),
                    }
                )
            return None

        # Entry logic: Sunday 18:00 to Monday 04:00 UTC
        is_sunday_entry = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 4)
        in_entry_window = is_sunday_entry or is_monday_early

        if not in_entry_window:
            return None

        if self.entry_week_id == current_week_id:
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Economic filters: short-term momentum intact and not severely overbought
        macro_regime = ctx.market.get("regime", "NORMAL")
        if macro_regime in ("CRISIS", "MELTDOWN"):
            return None

        fgi = ctx.features.get("fear_greed_index", 50)
        if fgi < 15:
            return None

        trend_bias = ema_fast >= ema_slow * 0.992
        rsi_valid = 32.0 <= rsi <= 68.0

        if trend_bias and rsi_valid:
            self.entry_week_id = current_week_id
            conf = 0.65 if rsi < 50.0 else 0.55

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_late_drift_accumulation",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "fear_greed": fgi,
                    "market_regime": macro_regime,
                }
            )

        return None