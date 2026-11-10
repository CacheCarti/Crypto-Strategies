from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MonthEndFlow(Strategy):
    METADATA = {
        "name": "MonthEndFlow",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 24 * 2,  # Up to 2 days hold
        "warmup_bars": 48,  # For 2 days of daily data, if needed, and initial price changes
        "required_features": ["eth_return_pct"] # To gauge daily decisiveness
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_daily_move_pct = 0.02 # 2% decisive move
        self.volume_confirm_multiplier = 1.5 # Volume must be 1.5x average
        self.avg_volume_period = 24 * 7 # 1 week of hourly bars for average volume
        self.cooldown_bars = 24 # Wait 24 hours after a trade
        self.last_trade_bar = -self.cooldown_bars - 1

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.avg_volume_period + 1)
        volumes = ctx.volumes(self.avg_volume_period)

        if len(closes) < self.avg_volume_period + 1 or len(volumes) < self.avg_volume_period:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_day = ctx.bar.timestamp.day
        current_month = ctx.bar.timestamp.month
        current_year = ctx.bar.timestamp.year

        # Determine last day of the month
        # Handle December separately
        if current_month == 12:
            last_day_of_month = 31
        else:
            # Get the first day of the next month, then subtract one day
            next_month = current_month + 1
            next_year = current_year
            if next_month > 12: # Should not happen with current_month == 12 handled
                next_month = 1
                next_year += 1
            
            # Simple approximation for last day of month
            # This is robust enough for typical month lengths
            if next_month in [2, 4, 6, 9, 11]: # Months with 30 or 28/29 days
                last_day_of_month = 30 if next_month != 2 else 28 # Simplified, assumes non-leap year for 2
            else: # Months with 31 days
                last_day_of_month = 31

        # Trading window: last two days of the month
        is_end_of_month = (current_day >= last_day_of_month - 1) and (current_day <= last_day_of_month)

        if not is_end_of_month:
            # If we have a position, check if it's the first day of the new month to exit
            if ctx.has_position() and current_day == 1 and ctx.bar.timestamp.hour == 0: # Exit on 1st day, 00:00 UTC
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "month_start_exit",
                    "current_day": current_day,
                    "current_month": current_month,
                    "exit_hour": ctx.bar.timestamp.hour
                })
            return None

        # Only enter if no position
        if ctx.has_position():
            return None

        # Check daily move decisiveness
        eth_daily_return_pct = ctx.features.get("eth_return_pct", 0.0)
        
        # Check volume confirmation
        avg_volume = self._sma(volumes, self.avg_volume_period)
        if avg_volume is None:
            return None
        
        current_volume = ctx.bar.volume
        volume_confirmed = current_volume > (avg_volume * self.volume_confirm_multiplier)

        signal_direction = None
        reason = ""
        confidence = 0.0

        if eth_daily_return_pct > self.min_daily_move_pct and volume_confirmed:
            signal_direction = "long"
            reason = "month_end_bullish_flow_decisive_move_volume_confirm"
            confidence = min(0.5 + abs(eth_daily_return_pct) * 10, 0.9) # Scale confidence by move size
        elif eth_daily_return_pct < -self.min_daily_move_pct and volume_confirmed:
            signal_direction = "short"
            reason = "month_end_bearish_flow_decisive_move_volume_confirm"
            confidence = min(0.5 + abs(eth_daily_return_pct) * 10, 0.9)

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "eth_daily_return_pct": eth_daily_return_pct,
                    "current_volume": current_volume,
                    "avg_volume": avg_volume,
                    "volume_confirm_multiplier": self.volume_confirm_multiplier,
                    "current_day": current_day,
                    "last_day_of_month": last_day_of_month
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already set last_trade_bar on flat signal, but good to ensure
        pass