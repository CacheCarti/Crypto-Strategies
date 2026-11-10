from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.last_traded_week = -1
        self.entry_price = 0.0

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
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour
        price = ctx.bar.close

        # Compute calendar week identifier to avoid duplicate weekend entries
        cal_week = ts.isocalendar()[1] + ts.year * 100

        # Check existing position management
        if ctx.has_position():
            # Scheduled Exit: Sunday late evening (20:00 - 23:00 UTC)
            if weekday == 6 and hour >= 21:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_scheduled_exit_sunday_night",
                        "price": price,
                        "weekday": weekday,
                        "hour": hour,
                    },
                )

            # Regime protection exit if sudden extreme volatility crisis hits
            if ctx.regime == "crisis":
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "crisis_regime_abort", "price": price},
                )
            return None

        # Mandatory cooldown check (at least 12 hours / 12 bars after an exit)
        if (ctx.bar_index - self.last_exit_bar) < 12:
            return None

        # Only one seasonal entry allowed per calendar weekend
        if self.last_traded_week == cal_week:
            return None

        # Event Entry Window: Friday 18:00 UTC to 23:00 UTC
        if weekday == 4 and 18 <= hour <= 23:
            # Macro / Market health checks: do not enter during extreme meltdown / crisis
            crisis_score = ctx.market.get("crisis_score", 0.0)
            if crisis_score > 0.65 or ctx.regime == "crisis":
                return None

            # Price trend & momentum confirmation filters
            ema20 = self._ema(closes, 20)
            rsi = self._rsi(closes, 14)
            if ema20 is None or rsi is None:
                return None

            # Avoid entering when heavily overbought or in a severe downtrend breakdown
            if rsi > 68.0 or price < (ema20 * 0.95):
                return None

            fear_greed = ctx.features.get("fear_greed_index", 50.0)

            # Scale confidence based on trend alignment and sentiment
            confidence = 0.65
            if price > ema20:
                confidence += 0.1
            if fear_greed >= 35.0:
                confidence += 0.1
            confidence = min(1.0, confidence)

            self.last_traded_week = cal_week
            self.entry_price = price

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=450.0,
                take_profit_bps=750.0,
                horizon_seconds=172800,
                metadata={
                    "reason": "friday_weekend_seasonality_inflow",
                    "price": price,
                    "ema20": round(ema20, 2),
                    "rsi": round(rsi, 2),
                    "fear_greed": fear_greed,
                    "crisis_score": round(crisis_score, 2),
                    "weekday": weekday,
                    "hour": hour,
                },
            )

        return None