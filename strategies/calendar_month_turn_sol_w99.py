from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMonthTransition(Strategy):
    METADATA = {
        "name": "SOL Month Transition",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours, allowing for event to play out
        "warmup_bars": 72, # For 3-day lookback at 1h bars
        "required_features": [], # No external features needed for this calendar-based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_month = None
        self.last_trade_bar = -self.METADATA["warmup_bars"] # Allow trading immediately after warmup
        self.cooldown_bars = 24 # Cooldown for 24 hours after a trade

        # Parameters for month transition logic
        self.transition_window_days = 3 # Look 3 days before and 3 days after month boundary
        self.directional_period = 24 # 24-hour period for daily direction check
        self.min_daily_move_pct = 0.5 # Minimum 0.5% move for decisive daily direction

    def _get_daily_direction(self, closes, period):
        if len(closes) < period:
            return None
        start_price = closes[-period]
        end_price = closes[-1]
        change_pct = ((end_price - start_price) / start_price) * 100
        if abs(change_pct) >= self.min_daily_move_pct:
            return "bullish" if change_pct > 0 else "bearish"
        return "neutral"

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.directional_period * self.transition_window_days * 2) # Get enough data for lookback
        highs = ctx.highs(self.directional_period * self.transition_window_days * 2)
        lows = ctx.lows(self.directional_period * self.transition_window_days * 2)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_month = ctx.bar.timestamp.month
        current_day = ctx.bar.timestamp.day
        current_year = ctx.bar.timestamp.year

        # Update last_month on first run or when month changes
        if self.last_month is None:
            self.last_month = current_month
            return None # Skip initial bar processing

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check if we are in a month transition period
        is_month_start_transition = current_day <= self.transition_window_days and current_month != self.last_month
        is_month_end_transition = current_day >= (self._days_in_month(self.last_month, current_year) - self.transition_window_days + 1) and current_month == self.last_month

        if is_month_start_transition or is_month_end_transition:
            # Check for decisive daily direction on both sides of the boundary
            # We need data from the previous month for month-start transition, and current month for month-end
            
            # For month-start, look at the last day of the previous month and first day of current
            if is_month_start_transition:
                # Approximate lookback for previous day's direction (last 24 bars of previous month)
                prev_month_closes = closes[:self.directional_period] # Assuming these are the last bars of previous month
                prev_month_direction = self._get_daily_direction(prev_month_closes, self.directional_period)

                # Current day's direction (first 24 bars of current month)
                current_day_closes = closes[self.directional_period:self.directional_period*2] # Approx first 24 bars of current month
                current_day_direction = self._get_daily_direction(current_day_closes, self.directional_period)

                if prev_month_direction == "bearish" and current_day_direction == "bullish":
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("long", confidence=0.75, metadata={
                            "reason": "month_start_bullish_reversal",
                            "prev_month_direction": prev_month_direction,
                            "current_day_direction": current_day_direction,
                            "price": ctx.bar.close,
                            "month": current_month,
                            "day": current_day
                        })
                elif prev_month_direction == "bullish" and current_day_direction == "bearish":
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("short", confidence=0.75, metadata={
                            "reason": "month_start_bearish_reversal",
                            "prev_month_direction": prev_month_direction,
                            "current_day_direction": current_day_direction,
                            "price": ctx.bar.close,
                            "month": current_month,
                            "day": current_day
                        })

            # For month-end, look at the last day of the current month and the day before it
            if is_month_end_transition:
                # Day before last day of month
                before_last_day_closes = closes[-self.directional_period*2:-self.directional_period]
                before_last_day_direction = self._get_daily_direction(before_last_day_closes, self.directional_period)

                # Last day of month
                last_day_closes = closes[-self.directional_period:]
                last_day_direction = self._get_daily_direction(last_day_closes, self.directional_period)

                if before_last_day_direction == "bearish" and last_day_direction == "bullish":
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("long", confidence=0.75, metadata={
                            "reason": "month_end_bullish_reversal",
                            "before_last_day_direction": before_last_day_direction,
                            "last_day_direction": last_day_direction,
                            "price": ctx.bar.close,
                            "month": current_month,
                            "day": current_day
                        })
                elif before_last_day_direction == "bullish" and last_day_direction == "bearish":
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("short", confidence=0.75, metadata={
                            "reason": "month_end_bearish_reversal",
                            "before_last_day_direction": before_last_day_direction,
                            "last_day_direction": last_day_direction,
                            "price": ctx.bar.close,
                            "month": current_month,
                            "day": current_day
                        })

        # Update last_month if it has changed
        if current_month != self.last_month:
            self.last_month = current_month

        return None

    def _days_in_month(self, month, year):
        if month == 2:
            return 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
        elif month in [4, 6, 9, 11]:
            return 30
        else:
            return 31

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index # Reset cooldown