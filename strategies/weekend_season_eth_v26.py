from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold across weekend
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.trend_ema_period = 48
        self.rsi_period = 14
        self.last_exit_bar = -100
        self.cooldown_bars = 12
        self.entry_traded_this_weekend = False
        self.last_seen_weekend_id = -1

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
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.trend_ema_period + 10)
        if len(closes) < self.trend_ema_period + 5:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = dt.hour

        # Unique identifier for the weekend: year and ISO calendar week
        cal = dt.isocalendar()
        current_weekend_id = cal[0] * 100 + cal[1]

        if current_weekend_id != self.last_seen_weekend_id:
            self.last_seen_weekend_id = current_weekend_id
            self.entry_traded_this_weekend = False

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit conditions: Late Sunday (>= 20:00 UTC) or early Monday (< 03:00 UTC)
        is_exit_window = (weekday == 6 and hour >= 20) or (weekday == 0 and hour < 3)

        if has_pos and pos_dir == "long":
            if is_exit_window:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_calendar_exit_sunday_night",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # If not holding, check for entries on Friday evening UTC (18:00 to 23:00)
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if self.entry_traded_this_weekend:
            return None

        # Friday entry window
        is_friday_entry_window = (weekday == 4 and 18 <= hour <= 23)

        if is_friday_entry_window and not has_pos:
            ema48 = self._ema(closes, self.trend_ema_period)
            rsi14 = self._rsi(closes, self.rsi_period)

            if ema48 is None or rsi14 is None:
                return None

            # Skip entering during severe meltdowns or deep downtrend breakdowns
            crisis_score = ctx.market.get("crisis_score", 0.0)
            regime = ctx.market.get("regime", "NORMAL")
            fg_index = ctx.features.get("fear_greed_index", 50)

            if regime == "MELTDOWN" or crisis_score > 0.75:
                return None

            # Price confirmation: must not be in a severe freefall (RSI > 32)
            # and holding around or above baseline EMA to avoid catching falling knives
            price = ctx.bar.close
            if rsi14 >= 32.0 and price >= (ema48 * 0.985):
                confidence = 0.70
                if price > ema48:
                    confidence += 0.10
                if fg_index > 25:
                    confidence += 0.05
                confidence = min(1.0, confidence)

                self.entry_traded_this_weekend = True
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "friday_evening_weekend_drift_entry",
                        "weekday": weekday,
                        "hour": hour,
                        "price": price,
                        "ema48": round(ema48, 2),
                        "rsi14": round(rsi14, 2),
                        "fear_greed": fg_index,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index