from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 500.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.traded_this_weekend = False
        self.last_weekend_id = -1

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
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour
        iso_year, iso_week, _ = dt.isocalendar()
        current_weekend_id = iso_year * 100 + iso_week

        if current_weekend_id != self.last_weekend_id:
            self.last_weekend_id = current_weekend_id
            self.traded_this_weekend = False

        has_pos = ctx.has_position()
        curr_price = ctx.bar.close

        if has_pos:
            is_sunday_exit = (weekday == 6 and hour >= 20) or (weekday == 0 and hour < 2)
            if is_sunday_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_target_window_ended",
                        "weekday": weekday,
                        "hour": hour,
                        "price": curr_price,
                    },
                )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if self.traded_this_weekend:
            return None

        is_friday_entry = (weekday == 4 and hour >= 18) or (weekday == 5 and hour < 2)
        if not is_friday_entry:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        if fear_greed < 15.0:
            return None

        sma30 = self._sma(closes, 30)
        rsi14 = self._rsi(closes, 14)
        if sma30 is None or rsi14 is None:
            return None

        if curr_price < sma30 * 0.94:
            return None

        if rsi14 > 68.0:
            return None

        self.traded_this_weekend = True

        confidence = 0.70
        if curr_price > sma30:
            confidence += 0.10
        if fear_greed >= 40.0:
            confidence += 0.05
        confidence = min(confidence, 0.95)

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=500.0,
            take_profit_bps=750.0,
            horizon_seconds=172800,
            metadata={
                "reason": "weekend_seasonal_drift_entry",
                "weekday": weekday,
                "hour": hour,
                "price": curr_price,
                "sma30": sma30,
                "rsi14": rsi14,
                "fear_greed": fear_greed,
            },
        )