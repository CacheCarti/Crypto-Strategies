from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMonthTurn(Strategy):
    METADATA = {
        "name": "SOL Month Turn",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # ~2 hours
        "warmup_bars": 72, # For 3-day lookback for daily change
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 48 # 2 days cooldown
        self.last_trade_bar = -self.cooldown_bars
        self.month_turn_window_hours = 12 # +/- 12 hours around month turn
        self.daily_change_period = 24 # 24 bars = 1 day for 1h chart

    def _get_daily_change(self, closes, period):
        if len(closes) < period + 1:
            return None
        # Compare current close to close 24 bars ago (approx 1 day)
        return (closes[-1] - closes[-period-1]) / closes[-period-1]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.daily_change_period * 3 + 1) # Need enough data for 3 days of daily change
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.has_position():
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_timestamp = ctx.bar.timestamp
        current_day = current_timestamp.day
        current_hour = current_timestamp.hour

        # Check for month-end or month-start
        is_month_end = False
        is_month_start = False

        # Month-end: last day of the month, within window
        # Check if current day is the last day of the month
        # A simple way: check if tomorrow's month is different from today's
        next_hour_ts = current_timestamp.replace(hour=current_hour + 1) if current_hour < 23 else current_timestamp.replace(day=current_day + 1, hour=0)
        if next_hour_ts.month != current_timestamp.month: # This means current_timestamp is in the last day of the month
            if current_hour >= (24 - self.month_turn_window_hours // 2): # Last X hours of the month
                is_month_end = True

        # Month-start: first day of the month, within window
        if current_day == 1:
            if current_hour < (self.month_turn_window_hours // 2): # First X hours of the month
                is_month_start = True

        if not (is_month_end or is_month_start):
            return None

        # Calculate daily price changes for the last few days
        # We need yesterday's daily change and the day before yesterday's
        # Daily change is (close_today - close_yesterday) / close_yesterday
        # We need closes from 3 days ago to yesterday
        if len(closes) < self.daily_change_period * 3 + 1: # 3 full days + current bar
            return None

        # Daily change for day X is (close[X] - close[X-1]) / close[X-1]
        # We need close from current bar (closes[-1]), 24h ago (closes[-25]), 48h ago (closes[-49])
        close_now = closes[-1]
        close_1d_ago = closes[-self.daily_change_period - 1]
        close_2d_ago = closes[-self.daily_change_period * 2 - 1]
        close_3d_ago = closes[-self.daily_change_period * 3 - 1]

        daily_change_today = (close_now - close_1d_ago) / close_1d_ago
        daily_change_yesterday = (close_1d_ago - close_2d_ago) / close_2d_ago
        daily_change_day_before_yesterday = (close_2d_ago - close_3d_ago) / close_3d_ago

        min_decisive_move_pct = 0.015 # 1.5% decisive move

        if is_month_end:
            # Look for strong move down into month-end
            # E.g., recent daily changes are negative and decisive
            if daily_change_today < -min_decisive_move_pct and \
               daily_change_yesterday < -min_decisive_move_pct and \
               daily_change_day_before_yesterday < -min_decisive_move_pct:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.6,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "month_end_reversion_after_strong_down",
                        "daily_change_today_pct": round(daily_change_today * 100, 2),
                        "daily_change_yesterday_pct": round(daily_change_yesterday * 100, 2),
                        "daily_change_day_before_yesterday_pct": round(daily_change_day_before_yesterday * 100, 2),
                        "timestamp": current_timestamp.isoformat()
                    }
                )

        if is_month_start:
            # Look for strong move up into month-start
            # E.g., recent daily changes are positive and decisive
            if daily_change_today > min_decisive_move_pct and \
               daily_change_yesterday > min_decisive_move_pct and \
               daily_change_day_before_yesterday > min_decisive_move_pct:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.6,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "month_start_reversion_after_strong_up",
                        "daily_change_today_pct": round(daily_change_today * 100, 2),
                        "daily_change_yesterday_pct": round(daily_change_yesterday * 100, 2),
                        "daily_change_day_before_yesterday_pct": round(daily_change_day_before_yesterday * 100, 2),
                        "timestamp": current_timestamp.isoformat()
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this calendar strategy post-close,
        # cooldown handles frequency.
        pass