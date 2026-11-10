from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 129600,  # ~36 hours holding horizon
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 6
        self.bars_in_position = 0
        self.ema_fast_len = 12
        self.ema_slow_len = 26
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 30:
            return None

        has_pos = ctx.has_position()
        price = ctx.bar.close

        # Track holding duration
        if has_pos:
            self.bars_in_position += 1
        else:
            self.bars_in_position = 0

        # Feature / indicators
        ema_fast = self._ema(closes, self.ema_fast_len)
        ema_slow = self._ema(closes, self.ema_slow_len)
        rsi = self._rsi(closes, self.rsi_len)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour

        # Exit logic
        if has_pos and ctx.position_direction() == "long":
            # Primary time-based weekend exit: Sunday late afternoon/evening (>= 18:00 UTC)
            # or Monday morning liquidity rollover
            is_weekend_exit_time = (weekday == 6 and hour >= 18) or (weekday == 0 and hour < 4)
            trend_weakness = ema_fast < ema_slow and rsi < 46.0

            if is_weekend_exit_time:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "weekend_calendar_exit",
                    "weekday": weekday,
                    "hour": hour,
                    "bars_held": self.bars_in_position,
                    "rsi": round(rsi, 2)
                })

            # Early dynamic de-risk exit on breakdown during hold
            if self.bars_in_position >= 8 and trend_weakness:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "trend_weakness_derisk",
                    "bars_held": self.bars_in_position,
                    "rsi": round(rsi, 2)
                })

            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Skip during severe market turmoil
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Weekend entry window:
        # Batch 1: Friday late afternoon/evening (16:00 - 23:00 UTC)
        # Batch 2: Early Saturday continuation pullback (02:00 - 10:00 UTC)
        is_friday_entry = (weekday == 4 and 16 <= hour <= 23)
        is_saturday_entry = (weekday == 5 and 2 <= hour <= 10)

        if not (is_friday_entry or is_saturday_entry):
            return None

        # Price-action validation: avoid buying downward freefalls, ensure reasonable momentum
        momentum_ok = price > (ema_slow * 0.992) and rsi >= 40.0 and rsi <= 68.0
        micro_trend = ema_fast >= (ema_slow * 0.998)

        if momentum_ok and micro_trend:
            confidence = 0.68
            if ema_fast > ema_slow and rsi > 50.0:
                confidence = 0.82

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=320.0,
                take_profit_bps=480.0,
                horizon_seconds=129600,
                metadata={
                    "reason": "friday_weekend_drift_long" if is_friday_entry else "saturday_morning_reentry_long",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": round(price, 2)
                }
            )

        return None