from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 18
        self.entry_bar_idx = -1
        self.traded_this_cycle = False
        self.last_cycle_week = -1

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar_idx = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if closes is None or len(closes) < 50:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        cycle_id = iso_year * 100 + iso_week

        if cycle_id != self.last_cycle_week:
            self.last_cycle_week = cycle_id
            self.traded_this_cycle = False

        if ctx.has_position():
            if self.entry_bar_idx < 0:
                self.entry_bar_idx = ctx.bar_index

            bars_held = ctx.bar_index - self.entry_bar_idx
            is_exit_window = (weekday == 0 and hour >= 21) or (weekday == 1)

            if is_exit_window or bars_held >= 26:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "monday_window_expired",
                        "bars_held": bars_held,
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close,
                    },
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if self.traded_this_cycle:
            return None

        # Window: Sunday 18:00 UTC through Monday 04:00 UTC
        in_entry_window = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 4)
        if not in_entry_window:
            return None

        # Exclude extreme market crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)
        rsi14 = self._rsi(closes, 14)

        if ema20 is None or ema50 is None or rsi14 is None:
            return None

        current_price = ctx.bar.close

        # Require short-term stabilization (not free-falling) and avoid severely overbought conditions
        rsi_valid = 32.0 <= rsi14 <= 68.0
        price_above_fast = current_price >= (ema20 * 0.985)
        trend_aligned = ema20 >= (ema50 * 0.98)

        if not (rsi_valid and price_above_fast and trend_aligned):
            return None

        funding = ctx.features.get("funding_rate_solusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50)

        # Skip if long crowding is extreme
        if funding > 0.0006:
            return None

        confidence = 0.65
        if rsi14 < 48.0:
            confidence += 0.10
        if fear_greed < 45:
            confidence += 0.10
        if funding <= 0.0:
            confidence += 0.05
        confidence = min(0.95, confidence)

        self.traded_this_cycle = True
        self.entry_bar_idx = ctx.bar_index

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=320.0,
            take_profit_bps=520.0,
            horizon_seconds=86400,
            metadata={
                "reason": "sunday_monday_drift_selective",
                "weekday": weekday,
                "hour": hour,
                "rsi14": round(rsi14, 2),
                "ema20": round(ema20, 2),
                "ema50": round(ema50, 2),
                "funding": funding,
                "fear_greed": fear_greed,
                "crisis_score": round(crisis_score, 3),
            },
        )