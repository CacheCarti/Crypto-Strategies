from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityEth(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold through Sunday
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.last_traded_week = -1
        self.min_cooldown_bars = 18

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday is 0, Friday is 4, Sunday is 6
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        week_key = iso_year * 100 + iso_week

        # Check for open position and planned weekend exit
        if ctx.has_position():
            # Exit Sunday evening between 20:00 and 23:00 UTC, or Monday early morning
            if (weekday == 6 and hour >= 20) or (weekday == 0 and hour >= 0):
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_drift_window_closed",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Cooldown guard after any manual/trailing stop exit
        if ctx.bar_index - self.last_exit_bar < self.min_cooldown_bars:
            return None

        # Strict entry boundary: Friday evening 18:00-23:00 UTC only, at most once per calendar week
        if weekday != 4 or hour < 18 or hour > 23:
            return None

        if self.last_traded_week == week_key:
            return None

        # Filter out extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Price-based sanity filters to avoid entering into freefalls or extreme tops
        ema30 = self._ema(closes, 30)
        rsi14 = self._rsi(closes, 14)
        if ema30 is None or rsi14 is None:
            return None

        # Do not buy if severely overbought (>72) or crashing far below trend (>6% below EMA30)
        if rsi14 > 72.0 or ctx.bar.close < ema30 * 0.94:
            return None

        self.last_traded_week = week_key

        # Modulate confidence based on trend alignment and momentum
        confidence = 0.65
        if ctx.bar.close >= ema30:
            confidence += 0.15
        if 40.0 <= rsi14 <= 60.0:
            confidence += 0.10
        confidence = min(1.0, confidence)

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "friday_evening_drift_entry",
                "weekday": weekday,
                "hour": hour,
                "rsi14": round(rsi14, 2),
                "ema30": round(ema30, 2),
                "price": round(ctx.bar.close, 2),
                "crisis_score": round(crisis_score, 3),
            },
        )