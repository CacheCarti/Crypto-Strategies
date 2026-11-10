from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FridayPositioning(Strategy):
    METADATA = {
        "name": "Friday Positioning BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2-3 hours
        "warmup_bars": 24, # Need at least 6 bars for recent price changes, plus some buffer
        "required_features": [], # No external features needed for this calendar strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_signal_bar = -999999 # Initialize far in the past to allow first trade immediately
        self.cooldown_bars = 12 # Wait 12 bars (12 hours) after a trade to avoid overtrading
        self.min_price_change_bps = 20 # Minimum 20 bps change in last 6 hours to consider decisive

    def _calculate_price_change_bps(self, closes, period):
        if len(closes) < period:
            return 0.0
        start_price = closes[-period]
        end_price = closes[-1]
        if start_price == 0:
            return 0.0
        return (end_price - start_price) / start_price * 10000

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        # Only consider trades around Friday close / Sunday open UTC
        # Friday (weekday 4): 20:00 - 23:00 UTC (hours 20, 21, 22)
        # Sunday (weekday 6): 20:00 - 23:00 UTC (hours 20, 21, 22)
        # We are looking for positioning *before* the weekend close or *into* the Sunday open.
        
        is_friday_late = (ctx.bar.timestamp.weekday() == 4 and ctx.bar.timestamp.hour >= 20)
        is_sunday_late = (ctx.bar.timestamp.weekday() == 6 and ctx.bar.timestamp.hour >= 20)

        if not (is_friday_late or is_sunday_late):
            return None

        # Check for decisive price action over the last 6 hours (6 bars)
        recent_closes = ctx.closes(6)
        if len(recent_closes) < 6:
            return None

        price_change_bps = self._calculate_price_change_bps(recent_closes, 6)

        # Long entry: Decisive upward move on Friday late or Sunday late
        if not ctx.has_position() and price_change_bps > self.min_price_change_bps:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.7,
                metadata={
                    "reason": "decisive_upward_positioning",
                    "price_change_bps_6h": price_change_bps,
                    "current_price": ctx.bar.close,
                    "timestamp": ctx.bar.timestamp.isoformat(),
                    "day_of_week": ctx.bar.timestamp.weekday(),
                    "hour_of_day": ctx.bar.timestamp.hour,
                }
            )

        # Short entry: Decisive downward move on Friday late or Sunday late
        if not ctx.has_position() and price_change_bps < -self.min_price_change_bps:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.7,
                metadata={
                    "reason": "decisive_downward_positioning",
                    "price_change_bps_6h": price_change_bps,
                    "current_price": ctx.bar.close,
                    "timestamp": ctx.bar.timestamp.isoformat(),
                    "day_of_week": ctx.bar.timestamp.weekday(),
                    "hour_of_day": ctx.bar.timestamp.hour,
                }
            )
        
        # Exit if holding and conditions change dramatically, or simply hold
        # For this strategy, we'll let the declared_hold_seconds and SL/TP manage exits primarily,
        # but a strong counter-move could be an early exit.
        if ctx.has_position():
            if ctx.position_direction() == "long" and price_change_bps < -self.min_price_change_bps * 0.5: # Half the threshold for counter-move
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "counter_move_exit_long",
                        "price_change_bps_6h": price_change_bps,
                        "current_price": ctx.bar.close,
                        "timestamp": ctx.bar.timestamp.isoformat(),
                    }
                )
            if ctx.position_direction() == "short" and price_change_bps > self.min_price_change_bps * 0.5: # Half the threshold for counter-move
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "counter_move_exit_short",
                        "price_change_bps_6h": price_change_bps,
                        "current_price": ctx.bar.close,
                        "timestamp": ctx.bar.timestamp.isoformat(),
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this calendar strategy on close
        pass