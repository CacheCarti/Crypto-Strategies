from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        week_key = iso_year * 100 + iso_week

        ema20 = self._ema(closes, 20)
        rsi = self._rsi(closes, 14)
        if ema20 is None or rsi is None:
            return None

        # Position Management
        if ctx.has_position():
            if self.entry_price == 0.0:
                pos = ctx.position
                self.entry_price = pos.get("entry_price", ctx.bar.close) if pos else ctx.bar.close

            # Scheduled weekend exit: Sunday between 19:00 and 23:00 UTC
            if weekday == 6 and hour >= 19:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_close_target_reached",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close,
                        "rsi": round(rsi, 2),
                    },
                )

            # Regime breakdown exit: Meltdown or Crisis in market context
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ["CRISIS", "MELTDOWN"]:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "regime_breakdown_emergency_exit",
                        "regime": regime,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Entry Eligibility & Cooldown
        if ctx.bar_index - self.last_exit_bar < 8:
            return None

        # Only one seasonal trade setup per calendar week
        if self.last_traded_week == week_key:
            return None

        # Friday Evening UTC window: 16:00 to 23:00 UTC
        is_friday_window = (weekday == 4 and 16 <= hour <= 23)
        if not is_friday_window:
            return None

        # Sentiment and regime quality gate (avoids OOS drawdowns during market crashes)
        fg_index = ctx.features.get("fear_greed_index", 50)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if fg_index < 18 or crisis_score > 0.65:
            return None

        # Price-action confirmation: must not be severely collapsing or overbought
        # Closes near/above short-term EMA or healthy oversold bounce
        trend_ok = ctx.bar.close >= (ema20 * 0.985)
        momentum_ok = 35.0 <= rsi <= 68.0

        if trend_ok and momentum_ok:
            self.last_traded_week = week_key
            self.entry_price = ctx.bar.close

            confidence = 0.65
            if ctx.bar.close > ema20 and 45.0 <= rsi <= 60.0:
                confidence = 0.80

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=450.0,
                take_profit_bps=750.0,
                horizon_seconds=172800,
                metadata={
                    "reason": "friday_weekend_seasonality_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "ema20": round(ema20, 2),
                    "fear_greed": fg_index,
                    "crisis_score": round(crisis_score, 2),
                },
            )

        return None