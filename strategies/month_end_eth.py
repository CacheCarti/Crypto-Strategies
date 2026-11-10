from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MonthEndFlow(Strategy):
    METADATA = {
        "name": "MonthEndFlow",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 24 * 3,  # Up to 3 days hold
        "warmup_bars": 48,  # Need enough data for daily return and volume
        "required_features": ["eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 0
        self.last_trade_bar_index = -self.METADATA["warmup_bars"] # Initialize to avoid trading immediately
        self.cooldown_duration = 24  # Wait 24 bars (1 day) after a trade before new entry

    def _get_daily_return_and_volume(self, ctx: BarContext) -> Optional[tuple[float, float, float]]:
        # Calculate daily return and average volume for the last 24 bars
        if len(ctx.closes(24)) < 24:
            return None, None, None

        first_close = ctx.closes(24)[0]
        last_close = ctx.closes(1)[0]
        daily_return_pct = ((last_close - first_close) / first_close) * 100 if first_close != 0 else 0

        avg_volume_24h = sum(ctx.volumes(24)) / 24
        
        # Compare current bar volume to 24h average
        current_volume = ctx.bar.volume
        volume_ratio = current_volume / avg_volume_24h if avg_volume_24h > 0 else 0

        return daily_return_pct, avg_volume_24h, volume_ratio

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_duration:
            return None

        current_day = ctx.bar.timestamp.day
        current_month = ctx.bar.timestamp.month
        current_year = ctx.bar.timestamp.year

        # Determine last day of the month
        if current_month == 12:
            last_day_of_month = 31
        else:
            last_day_of_month = (ctx.bar.timestamp.replace(month=current_month+1, day=1) - ctx.bar.timestamp.replace(day=1)).day

        # Check if it's the last two days of the month or the first day of the new month
        is_month_end_period = (current_day >= last_day_of_month - 1 and current_day <= last_day_of_month) or \
                              (current_day == 1 and ctx.bar.timestamp.hour == 0) # Only consider first hour of new month for exit

        if not is_month_end_period:
            if ctx.has_position() and ctx.position["metadata"].get("month_end_trade", False) and current_day > 1 and ctx.bar.timestamp.hour > 0:
                # Exit if held past the first hour of the new month
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "month_end_trade_hold_expired", "entry_day": ctx.position["metadata"].get("entry_day")})
            return None

        daily_return_pct, avg_volume_24h, volume_ratio = self._get_daily_return_and_volume(ctx)
        if daily_return_pct is None or avg_volume_24h is None:
            return None

        # Only consider entries on the last two days of the month
        if current_day >= last_day_of_month - 1 and current_day <= last_day_of_month:
            if not ctx.has_position():
                signal_direction: Optional[str] = None
                confidence = 0.0
                reason = ""

                # Decisive daily move and confirming volume
                if daily_return_pct > 1.5 and volume_ratio > 1.2: # 1.5% up move with 20% higher volume
                    signal_direction = "long"
                    confidence = min(0.8, 0.5 + (daily_return_pct / 5.0) + (volume_ratio / 3.0)) # Scale confidence
                    reason = "month_end_bullish_flow_strong_daily_move_volume_confirm"
                elif daily_return_pct < -1.5 and volume_ratio > 1.2: # 1.5% down move with 20% higher volume
                    signal_direction = "short"
                    confidence = min(0.8, 0.5 + (abs(daily_return_pct) / 5.0) + (volume_ratio / 3.0)) # Scale confidence
                    reason = "month_end_bearish_flow_strong_daily_move_volume_confirm"

                if signal_direction:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        signal_direction,
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": reason,
                            "daily_return_pct": daily_return_pct,
                            "volume_ratio": volume_ratio,
                            "entry_day": current_day,
                            "month_end_trade": True
                        }
                    )
        
        # Exit logic on the first day of the new month, specifically the first hour
        if ctx.has_position() and ctx.position["metadata"].get("month_end_trade", False) and \
           current_day == 1 and ctx.bar.timestamp.hour == 0:
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal("flat", confidence=0.7, metadata={"reason": "month_end_trade_exit_new_month_start", "entry_day": ctx.position["metadata"].get("entry_day")})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close
        self.last_trade_bar_index = ctx.bar_index
        pass