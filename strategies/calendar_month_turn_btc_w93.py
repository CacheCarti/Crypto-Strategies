from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MonthEndMomentumStrategy(Strategy):
    METADATA = {
        "name": "MonthEndMomentumStrategy",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # Aim for a few days hold
        "warmup_bars": 72,              # Need enough bars for 3-day momentum
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -self.METADATA["warmup_bars"] # Allow trading immediately after warmup
        self.cooldown_bars = 48 # Cooldown for 2 days after a trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(72) # Need 3 days (72 hours) of closes for momentum
        if len(closes) < 72:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_month = ctx.bar.timestamp.month
        current_day = ctx.bar.timestamp.day
        current_hour = ctx.bar.timestamp.hour

        # Check for month-end/month-start transition
        is_month_end = False
        is_month_start = False

        # If it's the last day of the month, and within a few hours of midnight
        if current_day == self._last_day_of_month(ctx.bar.timestamp.year, current_month) and current_hour >= 20: # 20:00 - 23:00 on month-end
            is_month_end = True
        # If it's the 1st day of the new month, and within a few hours of midnight
        elif current_day == 1 and current_hour <= 3: # 00:00 - 03:00 on month-start
            is_month_start = True

        if not (is_month_end or is_month_start):
            return None

        # Calculate 3-day momentum (72 bars)
        momentum_period = 72
        if len(closes) < momentum_period:
            return None
        
        # Simple momentum: current price vs price 3 days ago
        momentum_change = (closes[-1] - closes[0]) / closes[0]

        # Define thresholds for "decisive" momentum
        long_threshold = 0.02 # 2% gain in 3 days
        short_threshold = -0.02 # 2% loss in 3 days

        signal_direction = None
        reason = ""

        if not ctx.has_position():
            if is_month_end and momentum_change > long_threshold:
                signal_direction = "long"
                reason = "month_end_bullish_momentum"
            elif is_month_start and momentum_change < short_threshold:
                signal_direction = "short"
                reason = "month_start_bearish_momentum"
            # We can also consider flipping if already in position and opposite signal appears
            # For simplicity, we prioritize not having a position first.

        if signal_direction:
            self.last_trade_bar = ctx.bar_index # Update last trade bar
            return ctx.signal(
                signal_direction,
                confidence=0.7, # Moderate confidence for this event-driven strategy
                metadata={
                    "reason": reason,
                    "momentum_change_pct": momentum_change * 100,
                    "current_price": ctx.bar.close,
                    "bar_timestamp": str(ctx.bar.timestamp),
                    "is_month_end": is_month_end,
                    "is_month_start": is_month_start,
                }
            )
        
        # If already in a position, just hold. No early exits based on this logic.
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown if a position closes prematurely due to SL/TP or other factors
        # This ensures we can re-enter when conditions are met again after a forced exit.
        self.last_trade_bar = ctx.bar_index
        pass

    def _last_day_of_month(self, year, month):
        if month == 12:
            return 31
        return (ctx.bar.timestamp.replace(year=year, month=month + 1, day=1) - ctx.bar.timestamp.timedelta(days=1)).day