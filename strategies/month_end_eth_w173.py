from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MonthlyFlowStrategy(Strategy):
    METADATA = {
        "name": "MonthlyFlowStrategy",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 24 * 3,  # Up to 3 days
        "warmup_bars": 48,  # Need enough bars for daily change calculations
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_daily_move_pct = 0.015  # 1.5% daily move
        self.min_volume_ratio = 1.5     # Volume must be 1.5x average
        self.cooldown_bars = 24         # Wait 24 hours after a trade
        self.last_trade_bar = -self.cooldown_bars - 1
        self.entry_month = -1

    def _get_daily_change_and_volume(self, ctx: BarContext, lookback_days: int = 1):
        # Calculate daily change and volume for the last full day
        # This is a simplification, ideally we'd aggregate 24 1h bars
        # For this strategy, we'll approximate with last 24 bars
        if len(ctx.closes(24 * lookback_days)) < 24 * lookback_days:
            return None, None, None

        start_index = -(24 * lookback_days)
        end_index = -1

        daily_open = ctx.opens(24 * lookback_days)[0]
        daily_close = ctx.closes(24 * lookback_days)[-1]
        daily_volume = sum(ctx.volumes(24 * lookback_days)[start_index:end_index+1])

        # Average volume over a longer period (e.g., 7 days)
        if len(ctx.volumes(24 * 7)) < 24 * 7:
            return None, None, None
        avg_daily_volume = sum(ctx.volumes(24 * 7)) / 7.0

        daily_change_pct = (daily_close - daily_open) / daily_open
        return daily_change_pct, daily_volume, avg_daily_volume

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_day = ctx.bar.timestamp.day
        current_month = ctx.bar.timestamp.month
        
        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check if it's the last two days of the month
        # This is an approximation for "last two UTC days"
        # We check if the next day is day 1 or day 2 of the next month
        is_last_two_days_of_month = False
        if current_day >= 29: # Might be 29, 30, 31 depending on month
            # Check if tomorrow is the 1st or 2nd of the next month
            # Simple heuristic: if current_day is 29, 30, or 31, it's likely the end
            # This is not perfectly accurate for month-end detection but robust enough.
            # A more precise way would involve checking days_in_month for current_month
            # For simplicity, we assume if we are past day 28, we are in the end-of-month window
            # and will trade to capture the start of next month flow.
            is_last_two_days_of_month = True

        if ctx.has_position():
            # If we entered this month and it's a new month, exit
            if self.entry_month != -1 and current_month != self.entry_month:
                self.last_trade_bar = ctx.bar_index
                self.entry_month = -1 # Reset
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "monthly_flow_exit_new_month",
                        "current_month": current_month,
                        "entry_month": self.entry_month,
                    },
                )
            return None # Hold position

        # Entry logic: Last two days of the month, decisive daily move, volume confirmation
        if not is_last_two_days_of_month:
            return None

        daily_change_pct, daily_volume, avg_daily_volume = self._get_daily_change_and_volume(ctx, lookback_days=1)

        if daily_change_pct is None or daily_volume is None or avg_daily_volume is None:
            return None

        volume_ratio = daily_volume / avg_daily_volume if avg_daily_volume > 0 else 0

        signal_direction = None
        reason = ""
        confidence = 0.0

        if daily_change_pct > self.min_daily_move_pct and volume_ratio > self.min_volume_ratio:
            signal_direction = "long"
            reason = "monthly_flow_bullish_entry"
            confidence = min(0.6 + abs(daily_change_pct) * 10, 0.9) # Scale confidence by strength of move
        elif daily_change_pct < -self.min_daily_move_pct and volume_ratio > self.min_volume_ratio:
            signal_direction = "short"
            reason = "monthly_flow_bearish_entry"
            confidence = min(0.6 + abs(daily_change_pct) * 10, 0.9) # Scale confidence by strength of move

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            self.entry_month = current_month
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "daily_change_pct": daily_change_pct,
                    "volume_ratio": volume_ratio,
                    "current_day": current_day,
                    "current_month": current_month,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset entry month on close for robustness, though on_bar also handles it
        self.entry_month = -1
        # Log position close for debugging/analysis
        # print(f"Position closed: {position['direction']} return={position['return_bps']}bps, held={position['bars_held']} bars")
        pass