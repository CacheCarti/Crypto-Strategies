from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 500.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold (Friday evening to Sunday night)
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 12
        self.entry_price = 0.0

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
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(60)
        if len(closes) < 60:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()  # Monday is 0, Friday is 4, Sunday is 6
        hour = dt.hour
        price = ctx.bar.close

        ema50 = self._ema(closes, 50)
        rsi14 = self._rsi(closes, 14)
        if ema50 is None or rsi14 is None:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50)
        regime = ctx.market.get("regime", "NORMAL")

        # Exit logic for active position
        if ctx.has_position():
            # Calendar exit: late Sunday (20:00 - 23:00 UTC)
            if weekday == 6 and hour >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_calendar_exit_sunday",
                        "price": price,
                        "hour": hour,
                        "weekday": weekday,
                        "rsi": rsi14,
                    },
                )
            # Failsafe exit: Monday rollover
            if weekday == 0 and hour >= 0:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "weekend_window_expired_monday",
                        "price": price,
                        "hour": hour,
                        "weekday": weekday,
                    },
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry Window: Friday 18:00 to 23:00 UTC
        if weekday == 4 and 18 <= hour <= 23:
            # Skip in severe market breakdown
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            # Technical quality filters to avoid buying into severe weekend dumps:
            # 1. Price above EMA50 or RSI indicating mean reversion from oversold (RSI > 35)
            # 2. Fear/Greed not completely panicking (FG > 15)
            trend_support = price >= ema50 * 0.985
            rsi_valid = 35.0 <= rsi14 <= 70.0
            sentiment_valid = fear_greed >= 15

            if trend_support and rsi_valid and sentiment_valid:
                # Dynamic confidence based on trend alignment and market calm
                confidence = 0.65
                if price > ema50:
                    confidence += 0.15
                if regime == "NORMAL":
                    confidence += 0.10

                return ctx.signal(
                    "long",
                    confidence=min(1.0, confidence),
                    stop_loss_bps=500.0,
                    take_profit_bps=750.0,
                    horizon_seconds=172800,
                    metadata={
                        "reason": "friday_weekend_drift_entry",
                        "price": price,
                        "ema50": ema50,
                        "rsi14": rsi14,
                        "fear_greed": fear_greed,
                        "regime": regime,
                        "hour": hour,
                        "weekday": weekday,
                    },
                )

        return None