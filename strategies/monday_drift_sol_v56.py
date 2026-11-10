from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_bars = 35
        self.last_exit_bar = -100
        self.cooldown_bars = 8

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _ema(self, values, period):
        if len(values) < period:
            return values[-1] if values else 0.0
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()  # Monday=0, Sunday=6
        hour = dt.hour
        price = ctx.bar.close

        # Position management
        if ctx.has_position():
            # Seasonality exit: Tuesday 00:00 UTC or later
            if weekday == 1:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={"reason": "tuesday_seasonal_exit", "weekday": weekday, "hour": hour, "price": price}
                )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid entering during extreme market crises
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.7:
            return None

        # Entry Window: Sunday 18:00 UTC to 23:00 UTC
        if weekday == 6 and (18 <= hour <= 23):
            rsi = self._rsi(closes, 14)
            ema24 = self._ema(closes, 24)

            # Avoid buying heavily overbought assets; require price not to be severely breaking down
            if rsi < 62.0 and price >= (ema24 * 0.965):
                confidence = 0.70
                if price > ema24:
                    confidence += 0.15
                if rsi < 42.0:
                    confidence += 0.10

                return ctx.signal(
                    "long",
                    confidence=min(1.0, confidence),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sunday_late_drift_accumulation",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "ema24": round(ema24, 2),
                        "price": price,
                        "crisis_score": crisis_score
                    }
                )

        return None