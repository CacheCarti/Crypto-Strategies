from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 129600,  # ~36 hours max nominal hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.entry_bar_idx = 0

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
        closes = ctx.closes(35)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard after any trade closure
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday is 0, Sunday is 6
        hour = ts.hour
        price = ctx.bar.close

        sma24 = self._sma(closes, 24)
        rsi14 = self._rsi(closes, 14)
        fear_greed = ctx.features.get("fear_greed_index", 50)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Handle active position management / exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            
            # Scheduled weekend exit: Sunday late night (20:00 - 23:00 UTC)
            if weekday == 6 and hour >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "sunday_scheduled_exit",
                        "bars_held": bars_held,
                        "price": price,
                        "weekday": weekday,
                        "hour": hour,
                    },
                )

            # Adaptive dynamic exit: Overbought momentum peak during weekend drift
            if (weekday == 5 or weekday == 6) and rsi14 is not None and rsi14 >= 72.0 and bars_held >= 12:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_momentum_target_reached",
                        "rsi14": rsi14,
                        "bars_held": bars_held,
                        "price": price,
                    },
                )

            # Time-stop: maximum hold limit reached (Monday morning fallback)
            if weekday == 0 and hour >= 6:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "monday_hard_timestop",
                        "bars_held": bars_held,
                        "price": price,
                    },
                )

            return None

        # Entry logic: No open position
        # Filter severe market crises or extreme panic
        if crisis_score > 0.65 or fear_greed < 20:
            return None

        # Entry Window: Friday 16:00 to 23:00 UTC
        is_friday_entry_window = (weekday == 4 and 16 <= hour <= 23)

        if is_friday_entry_window:
            # Momentum / Trend confirmation: Price should not be in severe freefall
            # Must be holding above or near SMA24 and RSI showing positive setup (40 < RSI < 68)
            if sma24 is not None and rsi14 is not None:
                trend_ok = price >= (sma24 * 0.992)
                rsi_ok = 40.0 <= rsi14 <= 67.0

                if trend_ok and rsi_ok:
                    confidence = 0.65
                    if rsi14 > 50.0:
                        confidence += 0.10
                    if price > sma24:
                        confidence += 0.10
                    confidence = min(0.90, confidence)

                    self.entry_bar_idx = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=400.0,
                        take_profit_bps=600.0,
                        horizon_seconds=129600,
                        metadata={
                            "reason": "friday_seasonality_momentum_entry",
                            "price": price,
                            "sma24": sma24,
                            "rsi14": rsi14,
                            "fear_greed": fear_greed,
                            "weekday": weekday,
                            "hour": hour,
                        },
                    )

        return None