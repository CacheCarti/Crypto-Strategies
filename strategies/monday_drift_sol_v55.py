from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 64800,  # ~18 hours
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.last_entry_week = -1

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
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
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday is 0, Sunday is 6
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()

        # Exit conditions: Tuesday morning or late Monday US session close (Monday 22:00+ or Tuesday)
        if ctx.has_position():
            if weekday == 1 or (weekday == 0 and hour >= 22):
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "seasonal_window_closed",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Check cooldown from prior trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Only 1 seasonal entry attempt per calendar week
        if self.last_entry_week == iso_week:
            return None

        # Entry Window: Sunday 18:00 UTC through Monday 04:00 UTC
        is_sunday_late = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 4)
        if not (is_sunday_late or is_monday_early):
            return None

        # Avoid extreme panic meltdown regimes
        market_regime = str(ctx.market.get("regime", "NORMAL")).upper()
        if market_regime in ["CRISIS", "MELTDOWN"]:
            return None

        fgi = ctx.features.get("fear_greed_index", 50)
        if fgi < 18:  # Reject toxic liquidity cascades
            return None

        # Technical verification: Not heavily overbought, showing constructive price action
        rsi = self._rsi(closes, 14)
        ema24 = self._ema(closes, 24)
        if rsi is None or ema24 is None:
            return None

        # Prevent buying exhausted peaks (RSI < 65) and avoid severe breakdown (>3% below 24h EMA)
        if rsi > 65.0 or (ctx.bar.close < ema24 * 0.97):
            return None

        # Dynamic confidence based on trend support and market sentiment
        base_conf = 0.65
        if ctx.bar.close > ema24:
            base_conf += 0.15
        if 35 <= rsi <= 55:
            base_conf += 0.10
        confidence = min(0.95, base_conf)

        self.last_entry_week = iso_week

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=350.0,
            take_profit_bps=550.0,
            horizon_seconds=64800,
            metadata={
                "reason": "sunday_monday_seasonality_entry",
                "weekday": weekday,
                "hour": hour,
                "rsi": round(rsi, 2),
                "ema24": round(ema24, 2),
                "fgi": fgi,
                "close": ctx.bar.close,
            },
        )