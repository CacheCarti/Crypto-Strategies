from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold (Friday evening to Sunday evening)
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_period = 50
        self.last_traded_week = -1
        self.cooldown_bars = 24
        self.last_exit_bar = -100

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_period)
        if len(closes) < self.warmup_period:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        current_week_id = iso_year * 100 + iso_week

        # Check for active position management / Sunday exit
        if ctx.has_position():
            # Exit late Sunday (e.g., Sunday 20:00 to 23:00 UTC)
            if weekday == 6 and hour >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_drift_close_sunday",
                        "weekday": weekday,
                        "hour": hour,
                        "price": ctx.bar.close
                    }
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Ensure we only enter once per calendar weekend
        if self.last_traded_week == current_week_id:
            return None

        # Precise entry event boundary: Friday evening between 18:00 and 22:00 UTC
        if weekday == 4 and (18 <= hour <= 22):
            # Market regime check
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ["CRISIS", "MELTDOWN"]:
                return None

            # Price trend & momentum filters to avoid buying collapsing market
            sma50 = self._sma(closes, 50)
            rsi = self._rsi(closes, 14)

            if sma50 is None or rsi is None:
                return None

            # Event boundary entry filter:
            # Must not be severely overbought (RSI < 68) or in freefall (RSI > 32)
            # Price preferably respecting the medium-term trend
            price = ctx.bar.close
            if 32.0 < rsi < 68.0 and price >= sma50 * 0.97:
                self.last_traded_week = current_week_id
                confidence = 0.75 if price >= sma50 else 0.60
                
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=400.0,
                    take_profit_bps=600.0,
                    horizon_seconds=172800,
                    metadata={
                        "reason": "friday_weekend_drift_entry",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "sma50": round(sma50, 2),
                        "price": price
                    }
                )

        return None