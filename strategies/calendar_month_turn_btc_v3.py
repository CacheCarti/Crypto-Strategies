from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BTCMonthTurnStrategy(Strategy):
    METADATA = {
        "name": "BTC Month Turn Strategy",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours, short for a swing but aligns with event-driven
        "warmup_bars": 72, # Sufficient for 24-bar lookback for daily direction
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 24 * 3 # Cooldown for 3 days after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.trade_month = -1 # Track the month of the last trade
        self.last_month_end_signal_bar = -self.cooldown_bars
        self.last_month_start_signal_bar = -self.cooldown_bars
        self.daily_lookback_period = 24 # 24 hours for daily direction

    def _get_daily_direction(self, closes, period):
        if len(closes) < period:
            return None, None
        
        # Calculate daily return from start of period to end
        start_price = closes[-period]
        end_price = closes[-1]
        
        if start_price == 0: # Avoid division by zero
            return None, None
            
        daily_return = (end_price - start_price) / start_price
        
        if daily_return > 0.005: # 0.5% up
            return "bullish", daily_return
        elif daily_return < -0.005: # 0.5% down
            return "bearish", daily_return
        else:
            return "neutral", daily_return

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.daily_lookback_period + 1) # Need at least 24 bars for daily direction
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_month = ctx.bar.timestamp.month
        current_day = ctx.bar.timestamp.day
        current_hour = ctx.bar.timestamp.hour

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check for month-end logic
        is_month_end_hour = current_hour == 23 and current_day == self._get_days_in_month(ctx.bar.timestamp.year, current_month)
        
        if is_month_end_hour and ctx.bar_index - self.last_month_end_signal_bar >= self.cooldown_bars:
            # Look back for daily direction before month end
            prev_day_closes = ctx.closes(self.daily_lookback_period * 2) # Get enough bars for previous day
            if len(prev_day_closes) < self.daily_lookback_period * 2:
                return None
            
            prev_day_direction, prev_day_return = self._get_daily_direction(prev_day_closes[:-self.daily_lookback_period], self.daily_lookback_period)
            
            if prev_day_direction == "bullish":
                self.last_trade_bar = ctx.bar_index
                self.last_month_end_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long", 
                    confidence=0.7, 
                    metadata={
                        "reason": "month_end_bullish_momentum",
                        "prev_daily_return": prev_day_return,
                        "current_month": current_month,
                        "current_day": current_day,
                        "bar_index": ctx.bar_index
                    }
                )
            elif prev_day_direction == "bearish":
                self.last_trade_bar = ctx.bar_index
                self.last_month_end_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short", 
                    confidence=0.7, 
                    metadata={
                        "reason": "month_end_bearish_momentum",
                        "prev_daily_return": prev_day_return,
                        "current_month": current_month,
                        "current_day": current_day,
                        "bar_index": ctx.bar_index
                    }
                )

        # Check for month-start logic
        is_month_start_hour = current_hour == 0 and current_day == 1
        
        if is_month_start_hour and ctx.bar_index - self.last_month_start_signal_bar >= self.cooldown_bars:
            # Look for daily direction on the first day of the month
            current_day_closes = ctx.closes(self.daily_lookback_period)
            if len(current_day_closes) < self.daily_lookback_period:
                return None # Not enough bars yet for the first day's direction
            
            current_day_direction, current_day_return = self._get_daily_direction(current_day_closes, self.daily_lookback_period)

            if current_day_direction == "bullish":
                self.last_trade_bar = ctx.bar_index
                self.last_month_start_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long", 
                    confidence=0.7, 
                    metadata={
                        "reason": "month_start_bullish_momentum",
                        "current_daily_return": current_day_return,
                        "current_month": current_month,
                        "current_day": current_day,
                        "bar_index": ctx.bar_index
                    }
                )
            elif current_day_direction == "bearish":
                self.last_trade_bar = ctx.bar_index
                self.last_month_start_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short", 
                    confidence=0.7, 
                    metadata={
                        "reason": "month_start_bearish_momentum",
                        "current_daily_return": current_day_return,
                        "current_month": current_month,
                        "current_day": current_day,
                        "bar_index": ctx.bar_index
                    }
                )

        return None

    def _get_days_in_month(self, year, month):
        if month == 2:
            return 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
        elif month in [4, 6, 9, 11]:
            return 30
        else:
            return 31

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to apply cooldown after any position close
        self.last_trade_bar = ctx.bar_index