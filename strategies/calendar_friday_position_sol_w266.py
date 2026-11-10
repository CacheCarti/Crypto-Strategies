from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendPositioning(Strategy):
    METADATA = {
        "name": "WeekendPositioningSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours intended hold
        "warmup_bars": 24, # Need 6 hours of bars for decision + buffer
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12 # Wait 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars * 2 # Initialize to allow immediate trade if conditions met
        self.max_position_per_weekend = 1
        self.weekend_trades_count = 0
        self.last_weekend_start_bar = -1

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(24) # Need at least 6 hours for decision, plus some for SMA
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Reset weekend trade count at the start of a new weekend cycle (Friday 00:00 UTC)
        if current_weekday == 4 and current_hour == 0 and ctx.bar_index != self.last_weekend_start_bar:
            self.weekend_trades_count = 0
            self.last_weekend_start_bar = ctx.bar_index

        # Entry logic: Late Friday / Early Saturday for long positioning
        # We are looking for a decisive move in the last 6 hours leading into late Friday UTC
        is_late_friday = (current_weekday == 4 and current_hour >= 18) # Friday 18:00 UTC onwards
        is_early_saturday = (current_weekday == 5 and current_hour < 6) # Saturday 00:00 UTC to 05:00 UTC

        if not (is_late_friday or is_early_saturday):
            # If we have an open position and it's Sunday evening, consider flattening
            if ctx.has_position() and ctx.position_direction() == "long" and \
               current_weekday == 6 and current_hour >= 18: # Sunday 18:00 UTC onwards
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "sunday_evening_exit", "hour": current_hour, "weekday": current_weekday})
            return None

        if ctx.has_position():
            return None

        # Limit to one position per weekend
        if self.weekend_trades_count >= self.max_position_per_weekend:
            return None

        # Check for decisive move in the last 6 hours
        # Price change over the last 6 bars (6 hours)
        six_hour_price_change = (closes[-1] - closes[-7]) / closes[-7] if len(closes) >= 7 else 0.0
        
        # Simple Moving Average for trend confirmation
        sma_24 = self._sma(closes, 24) # 24-hour SMA
        sma_6 = self._sma(closes, 6)   # 6-hour SMA

        if sma_24 is None or sma_6 is None:
            return None

        # Conditions for a long signal:
        # 1. Price has moved up significantly in the last 6 hours (e.g., > 1.5%)
        # 2. Short-term SMA (6-hr) is above longer-term SMA (24-hr)
        # 3. Current price is above the 6-hour SMA
        if six_hour_price_change > 0.015 and \
           sma_6 > sma_24 and \
           ctx.bar.close > sma_6:

            self.last_trade_bar = ctx.bar_index
            self.weekend_trades_count += 1
            return ctx.signal(
                "long",
                confidence=0.75,
                metadata={
                    "reason": "late_friday_positioning_long",
                    "hour": current_hour,
                    "weekday": current_weekday,
                    "six_hr_price_change_pct": six_hour_price_change * 100,
                    "sma6": sma_6,
                    "sma24": sma_24,
                    "close": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We can log or adjust future behavior here if needed.
        # For this strategy, simple cooldown is managed in on_bar.
        pass