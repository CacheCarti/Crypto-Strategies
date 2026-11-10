from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MonthTurnMomentum(Strategy):
    METADATA = {
        "name": "MonthTurnMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours, allowing for some price action
        "warmup_bars": 48, # Enough for 2-day price action
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 48 # 2 days cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.month_turn_window = 24 # 24 hours before/after month end
        self.momentum_period = 24 # 24-hour momentum for decision
        self.min_momentum_pct = 0.005 # 0.5% decisive move over 24 hours
        self.last_month_checked = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_month = ctx.bar.timestamp.month
        current_day = ctx.bar.timestamp.day
        current_hour = ctx.bar.timestamp.hour

        # Only check month turn logic once per month to avoid re-triggering
        if current_month == self.last_month_checked:
            return None
        
        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check for month end (last day of month) or month start (first day of month)
        is_month_end = False
        is_month_start = False

        # Determine last day of month
        if current_month == 2: # February
            last_day = 29 if ctx.bar.timestamp.year % 4 == 0 else 28
        elif current_month in [4, 6, 9, 11]: # April, June, Sept, Nov
            last_day = 30
        else:
            last_day = 31

        # Check if we are in the month-end window
        if current_day == last_day and current_hour >= 24 - self.month_turn_window:
            is_month_end = True
        
        # Check if we are in the month-start window
        if current_day == 1 and current_hour < self.month_turn_window:
            is_month_start = True

        if not (is_month_end or is_month_start):
            return None

        # We are in a month-turn window, now check momentum
        if len(closes) < self.momentum_period + 1:
            return None

        # Momentum calculation: price change over the last momentum_period
        price_at_start_of_period = closes[-(self.momentum_period + 1)]
        current_price = ctx.bar.close
        momentum_pct = (current_price - price_at_start_of_period) / price_at_start_of_period

        signal_direction = None
        confidence = 0.0

        if momentum_pct > self.min_momentum_pct: # Decisive upward move
            if not ctx.has_position() or ctx.position_direction() == "short":
                signal_direction = "long"
                confidence = min(0.7 + (momentum_pct / (self.min_momentum_pct * 2)), 0.9) # Scale confidence
                self.last_month_checked = current_month
        elif momentum_pct < -self.min_momentum_pct: # Decisive downward move
            if not ctx.has_position() or ctx.position_direction() == "long":
                signal_direction = "short"
                confidence = min(0.7 + (abs(momentum_pct) / (self.min_momentum_pct * 2)), 0.9) # Scale confidence
                self.last_month_checked = current_month

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            reason = f"month_turn_momentum_{signal_direction}"
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "momentum_pct": momentum_pct,
                    "month": current_month,
                    "day": current_day,
                    "hour": current_hour,
                    "is_month_end": is_month_end,
                    "is_month_start": is_month_start,
                }
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_month_checked to -1 to allow for a new trade in the same month if cooldown permits
        # This is important if a position is closed early, and a new setup appears later in the month-turn window
        # However, for this strategy, we only want one trade per month-turn event, so we keep it.
        # self.last_month_checked = -1 # Keep commented out to enforce one trade per month-turn
        pass