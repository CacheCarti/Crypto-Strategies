from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "Weekend Seasonality ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -100
        self.cooldown_bars = 48
        self.entry_bar = 0

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday is 0, Friday is 4, Sunday is 6
        hour = ts.hour
        price = ctx.bar.close

        # Position Management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            
            # Scheduled event boundary exit: Sunday evening (20:00 - 23:00 UTC)
            if weekday == 6 and hour >= 20:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "sunday_window_close", "bars_held": bars_held, "price": price}
                )

            # Max duration emergency cut (~52 hours)
            if bars_held >= 52:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "max_weekend_duration_reached", "bars_held": bars_held, "price": price}
                )

            return None

        # Entry Window: Friday 18:00 to 22:00 UTC
        is_friday_entry_window = (weekday == 4 and 18 <= hour <= 22)
        if not is_friday_entry_window:
            return None

        # Hard Cooldown to ensure strictly 1 trade per weekend
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Technical & Sentiment filters to avoid entering during breakdowns
        trend_ma = self._sma(closes, 30)
        rsi = self._rsi(closes, 14)
        fg_index = ctx.features.get("fear_greed_index", 50)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        if trend_ma is None or rsi is None:
            return None

        # Filter out extreme panic or severe downtrends entering the weekend
        if crisis_score > 0.65 or fg_index < 18:
            return None

        # Filter: Price should not be in freefall (RSI > 35) and not extremely overextended (RSI < 72)
        if not (35.0 <= rsi <= 72.0):
            return None

        # Filter: Price must be reasonably resilient relative to local average (> 0.985 of SMA)
        if price < trend_ma * 0.985:
            return None

        self.last_trade_bar = ctx.bar_index
        self.entry_bar = ctx.bar_index

        confidence = 0.70
        if price > trend_ma and rsi > 45.0:
            confidence = 0.85

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
                "trend_ma": round(trend_ma, 2),
                "rsi": round(rsi, 2),
                "fear_greed": fg_index
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index