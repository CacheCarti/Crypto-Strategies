from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MonthEndMonthStartMomentum(Strategy):
    METADATA = {
        "name": "MonthEndMonthStartMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours hold
        "warmup_bars": 48, # Need enough bars for 2 days of daily close data
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 24 # Cooldown for 24 hours after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.last_month_transition_bar = -self.cooldown_bars
        self.daily_closes = [] # To store daily closing prices for momentum check

    def _get_daily_close(self, ctx: BarContext, offset_days: int) -> Optional[float]:
        """
        Gets the closing price of a previous day.
        offset_days=0 for current day, 1 for yesterday, etc.
        """
        # This is a simplification. In a real scenario, you'd need to store daily closes.
        # For this strategy, we'll approximate by checking closes from 24 * offset_days bars ago
        # assuming 1-hour bars.
        bar_offset = 24 * offset_days
        if ctx.bar_index < bar_offset:
            return None
        return ctx.closes(bar_offset + 1)[0] # Get the close from 'bar_offset' bars ago

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day
        
        # Check for cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only check for new signals if not already in a position
        if ctx.has_position():
            return None

        # Only trigger around month transition (last 6 hours of month, first 6 hours of next month)
        is_month_end_window = (current_day == 1) and (current_hour >= 0 and current_hour < 6) # First 6 hours of new month
        
        # Approximate month end: check if the next bar's day will be 1 and it's end of current month
        # This requires looking ahead, which is not directly possible.
        # Instead, we check if the current bar is in the first 6 hours of the new month.
        # We need a robust way to know if 'yesterday' was the end of the previous month.
        
        # Simple heuristic: if it's the 1st of the month AND we haven't traded this transition recently
        if current_day == 1 and (ctx.bar_index - self.last_month_transition_bar > self.cooldown_bars):
            # We are in the first day of a new month. Check momentum from previous month's end.
            # Get approximate daily closes for momentum check
            # Day -1: Last day of previous month
            # Day -2: Second to last day of previous month
            # Day 1: First day of current month (today)
            
            # Since we only have 1h bars, we'll approximate daily closes from the history.
            # This is a simplification. A more robust way would be to store daily closes.
            
            # Get close for day -1 (approx 24-48 hours ago)
            close_day_minus_1 = ctx.closes(48)[-25] if len(ctx.closes(48)) >= 25 else None # Close of the day before yesterday
            
            # Get close for day -2 (approx 48-72 hours ago)
            close_day_minus_2 = ctx.closes(72)[-49] if len(ctx.closes(72)) >= 49 else None # Close of the day before that

            # Get today's open for initial momentum of the new month
            today_open = ctx.bar.open

            if close_day_minus_1 is None or close_day_minus_2 is None:
                return None

            # Momentum check:
            # Bullish: Day -2 to Day -1 was up, and Day -1 to Today's Open was up
            # Bearish: Day -2 to Day -1 was down, and Day -1 to Today's Open was down

            bullish_momentum = (close_day_minus_1 > close_day_minus_2) and (today_open > close_day_minus_1)
            bearish_momentum = (close_day_minus_1 < close_day_minus_2) and (today_open < close_day_minus_1)

            confidence = 0.6 # Base confidence for this event

            if bullish_momentum:
                self.last_trade_bar = ctx.bar_index
                self.last_month_transition_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "month_start_bullish_momentum",
                        "close_day_minus_2": close_day_minus_2,
                        "close_day_minus_1": close_day_minus_1,
                        "today_open": today_open,
                        "current_day": current_day,
                        "current_hour": current_hour
                    }
                )
            elif bearish_momentum:
                self.last_trade_bar = ctx.bar_index
                self.last_month_transition_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "month_start_bearish_momentum",
                        "close_day_minus_2": close_day_minus_2,
                        "close_day_minus_1": close_day_minus_1,
                        "today_open": today_open,
                        "current_day": current_day,
                        "current_hour": current_hour
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to ensure cooldown is applied
        self.last_trade_bar = ctx.bar_index
        # Log details of the closed position
        # print(f"Position closed: {position['direction']} at {ctx.bar.timestamp}. Return: {position['return_bps']} bps, Held: {position['bars_held']} bars.")