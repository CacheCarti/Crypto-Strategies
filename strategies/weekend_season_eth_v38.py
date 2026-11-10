from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48h weekend hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_period = 50
        self.cooldown_bars = 72  # Minimum 3 days before next cycle
        self.last_exit_bar = -100
        self.last_traded_week = -1

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
        closes = ctx.closes(self.warmup_period)
        if len(closes) < self.warmup_period:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday is 0, Sunday is 6
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        current_week_id = iso_year * 100 + iso_week

        # Check exits first if long position is held
        if ctx.has_position():
            # Scheduled event boundary exit: Sunday 20:00 - 23:00 UTC
            if weekday == 6 and hour >= 20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_drift_close_sunday_evening",
                        "price": ctx.bar.close,
                        "weekday": weekday,
                        "hour": hour
                    }
                )
            # Monday morning safety flush if still holding
            if weekday == 0 and hour >= 0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "monday_cutoff_safety_exit",
                        "price": ctx.bar.close,
                        "weekday": weekday,
                        "hour": hour
                    }
                )
            return None

        # Hard cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Prevent duplicate entries in the same calendar week
        if current_week_id == self.last_traded_week:
            return None

        # Entry window: Friday 18:00 to 23:00 UTC
        is_friday_entry_window = (weekday == 4 and 18 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        # Technical & structural filters:
        # Avoid buying during extreme macro crash or severe bear momentum
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("MELTDOWN", "CRISIS"):
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50)
        if fear_greed < 15:
            return None

        ema_trend = self._ema(closes, 30)
        rsi = self._rsi(closes, 14)

        if ema_trend is None or rsi is None:
            return None

        current_price = ctx.bar.close

        # Require reasonable trend/momentum support: price not heavily detached downward and RSI not severely oversold/broken
        if current_price < ema_trend * 0.96 or rsi > 72.0 or rsi < 32.0:
            return None

        # Compute dynamic conviction confidence
        confidence = 0.65
        if current_price > ema_trend:
            confidence += 0.15
        if 40.0 <= rsi <= 60.0:
            confidence += 0.10
        confidence = min(1.0, max(0.1, confidence))

        self.last_traded_week = current_week_id

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=450.0,
            take_profit_bps=750.0,
            horizon_seconds=172800,
            metadata={
                "reason": "friday_weekend_expansion_long",
                "price": current_price,
                "ema30": round(ema_trend, 2),
                "rsi": round(rsi, 2),
                "fear_greed": fear_greed,
                "weekday": weekday,
                "hour": hour,
                "week_id": current_week_id
            }
        )