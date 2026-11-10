from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.last_traded_week = -1
        self.cooldown_bars = 12

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        current_week_id = iso_year * 100 + iso_week

        # Manage existing position: scheduled weekend unwind late Sunday (20:00 UTC+)
        if ctx.has_position():
            if weekday == 6 and hour >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "sunday_scheduled_unwind", "hour": hour, "weekday": weekday}
                )
            return None

        # Post-exit mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Prevent duplicate entries in the same calendar weekend
        if self.last_traded_week == current_week_id:
            return None

        # Entry window: Friday between 18:00 and 23:00 UTC
        if weekday != 4 or not (18 <= hour <= 23):
            return None

        # High-conviction filters to eliminate marginal setups and oos drift failure:
        # 1. Market macro regime should not be crisis/meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # 2. Funding rate check: avoid crowded perp longs going into weekend
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        if funding > 0.0003:  # > 0.03% per 8h suggests excessive froth
            return None

        # 3. Trend and momentum filters
        ema50 = self._ema(closes, 50)
        rsi14 = self._rsi(closes, 14)

        if ema50 is None or rsi14 is None:
            return None

        current_price = ctx.bar.close

        # Require underlying trend stability (price not severely breaking down below EMA50)
        # and RSI showing constructive momentum without being overextended
        if current_price < ema50 * 0.985:
            return None
        if rsi14 < 42.0 or rsi14 > 68.0:
            return None

        # High-conviction entry signal validated
        self.last_traded_week = current_week_id
        confidence = 0.75 if current_price >= ema50 else 0.60

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=400.0,
            take_profit_bps=750.0,
            horizon_seconds=172800,
            metadata={
                "reason": "weekend_seasonal_drift_entry",
                "rsi": round(rsi14, 2),
                "ema50": round(ema50, 2),
                "funding": funding,
                "price": current_price,
                "hour": hour,
                "weekday": weekday,
            }
        )