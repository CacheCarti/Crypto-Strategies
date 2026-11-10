from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MonthTurnETH(Strategy):
    METADATA = {
        "name": "MonthTurnETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 48, # Need enough bars for 24-hour lookback
        "required_features": ["eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_hours = 24
        self.cooldown_bars = 48 # 2 days cooldown
        self.last_trade_bar = -self.cooldown_bars
        self.last_month_checked = -1 # To ensure we only trigger once per month turn
        self.daily_move_threshold = 0.015 # 1.5% daily move
        self.month_turn_window_hours = 2 # Hours before/after month turn to consider

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_hours + 1)
        if len(closes) < self.lookback_hours + 1:
            return None

        current_month = ctx.bar.timestamp.month
        current_day = ctx.bar.timestamp.day
        current_hour = ctx.bar.timestamp.hour

        # Only check for month turn around the boundary
        if not (current_day == 1 and current_hour < self.month_turn_window_hours) and \
           not (current_day > 25 and current_hour >= 24 - self.month_turn_window_hours):
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Ensure we only check this specific month turn once
        if current_month == self.last_month_checked:
            return None

        # Check for month-end
        is_month_end = (ctx.bar.timestamp.day == 1 and current_hour < self.month_turn_window_hours) # First few hours of new month
        
        if is_month_end:
            # Look back 24 hours from the end of previous month
            # Calculate daily return for the last day of previous month
            # We need to approximate this from 1-hour bars
            
            # Get closes for the last 24 hours of the *previous* month
            # This requires checking the timestamp of each bar to ensure it belongs to the previous month's last day
            prev_month_closes = []
            for i in range(1, self.lookback_hours + 1):
                # This is a bit tricky with 1-hour bars. We'll approximate by taking the 24 bars
                # immediately preceding the current bar.
                # If we are on Jan 1st 00:00, we look at Dec 31st 00:00 to Dec 31st 23:00
                # We assume ctx.closes(N) gives bars ending at current bar, so closes[0] is oldest
                pass # The eth_return_pct feature is better for this.

            # Use eth_return_pct for daily direction
            eth_daily_return = ctx.features.get("eth_return_pct", 0.0)

            # Condition: Strong positive move on previous day, suggesting bullish continuation
            if eth_daily_return > self.daily_move_threshold:
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    self.last_month_checked = current_month
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "month_start_bullish_momentum",
                            "eth_daily_return_pct": eth_daily_return,
                            "current_month": current_month,
                            "current_day": current_day,
                            "current_hour": current_hour
                        }
                    )
            # Condition: Strong negative move on previous day, suggesting bearish continuation
            elif eth_daily_return < -self.daily_move_threshold:
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    self.last_month_checked = current_month
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "month_start_bearish_momentum",
                            "eth_daily_return_pct": eth_daily_return,
                            "current_month": current_month,
                            "current_day": current_day,
                            "current_hour": current_hour
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy
        pass