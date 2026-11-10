from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMonthTransition(Strategy):
    METADATA = {
        "name": "SOL Month Transition",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 24 * 3,  # Up to 3 days hold for event
        "warmup_bars": 30,  # For SMA
        "required_features": ["sol_return_pct"], # For daily direction
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 48  # 2 days cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars
        self.sma_period = 20 # For daily direction check

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.sma_period + 1)
        if len(closes) < self.sma_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar_index + self.cooldown_bars:
            return None

        current_day = ctx.bar.timestamp.day
        current_month = ctx.bar.timestamp.month
        current_year = ctx.bar.timestamp.year

        is_month_end_window = False
        is_month_start_window = False

        # Define month-end window (last 2 days of month)
        # Handle December for month roll-over
        if current_month == 12:
            next_month_day = 1
            next_month = 1
            next_year = current_year + 1
        else:
            next_month_day = 1
            next_month = current_month + 1
            next_year = current_year

        # Get number of days in current month
        if current_month in [1, 3, 5, 7, 8, 10, 12]:
            days_in_month = 31
        elif current_month in [4, 6, 9, 11]:
            days_in_month = 30
        else: # February
            days_in_month = 29 if current_year % 4 == 0 and (current_year % 100 != 0 or current_year % 400 == 0) else 28

        # Check if we are in the last 2 days of the month
        if days_in_month - current_day <= 1: # Last 2 days
            is_month_end_window = True

        # Check if we are in the first 2 days of the month
        if current_day <= 2:
            is_month_start_window = True

        # Only consider trading if we are in one of these windows
        if not (is_month_end_window or is_month_start_window):
            return None

        # Get daily SOL return percentage
        sol_return_pct = ctx.features.get("sol_return_pct", 0.0)

        # Calculate SMA for trend confirmation
        sma_val = self._sma(closes, self.sma_period)
        if sma_val is None:
            return None

        # Daily direction check: price above/below SMA and strong daily return
        is_bullish_day = closes[-1] > sma_val and sol_return_pct > 0.5
        is_bearish_day = closes[-1] < sma_val and sol_return_pct < -0.5

        signal_direction = None
        reason = ""
        confidence = 0.0
        metadata = {}

        if not ctx.has_position():
            # Long entry logic: Bullish day at month-end or month-start
            if is_month_end_window and is_bullish_day:
                # Require price to be above the SMA and a positive daily move
                signal_direction = "long"
                reason = "month_end_bullish_momentum"
                confidence = 0.75
                metadata = {
                    "reason": reason,
                    "current_day": current_day,
                    "current_month": current_month,
                    "sol_return_pct": sol_return_pct,
                    "close": closes[-1],
                    "sma": sma_val,
                }
            elif is_month_start_window and is_bullish_day:
                signal_direction = "long"
                reason = "month_start_bullish_momentum"
                confidence = 0.75
                metadata = {
                    "reason": reason,
                    "current_day": current_day,
                    "current_month": current_month,
                    "sol_return_pct": sol_return_pct,
                    "close": closes[-1],
                    "sma": sma_val,
                }

            # Short entry logic: Bearish day at month-end or month-start
            elif is_month_end_window and is_bearish_day:
                signal_direction = "short"
                reason = "month_end_bearish_momentum"
                confidence = 0.75
                metadata = {
                    "reason": reason,
                    "current_day": current_day,
                    "current_month": current_month,
                    "sol_return_pct": sol_return_pct,
                    "close": closes[-1],
                    "sma": sma_val,
                }
            elif is_month_start_window and is_bearish_day:
                signal_direction = "short"
                reason = "month_start_bearish_momentum"
                confidence = 0.75
                metadata = {
                    "reason": reason,
                    "current_day": current_day,
                    "current_month": current_month,
                    "sol_return_pct": sol_return_pct,
                    "close": closes[-1],
                    "sma": sma_val,
                }

        if signal_direction:
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar index to enable cooldown
        self.last_trade_bar_index = ctx.bar_index
        # You could also add adaptive logic here, e.g., adjust confidence based on recent performance
        pass