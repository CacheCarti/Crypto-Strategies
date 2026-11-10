from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendRebuildETH(Strategy):
    METADATA = {
        "name": "Weekend Rebuild ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours intended hold time
        "warmup_bars": 72, # For 3-day lookback + EMA
    }

    def initialize(self, ctx: BarContext) -> None:
        self.weekend_lookback_bars = 48 # Fri 12:00 UTC to Sun 12:00 UTC = 48 hours for 1h bars
        self.monday_entry_hour_start = 12 # 12:00 UTC Monday (US morning)
        self.monday_entry_hour_end = 16   # 16:00 UTC Monday (US afternoon)
        self.cooldown_bars = 24 # Do not trade for 24 hours after a position closes
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow trade immediately

        # EMA for trend confirmation
        self.ema_period = 24 # 24-hour EMA

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        current_time = ctx.bar.timestamp
        current_weekday = current_time.weekday() # Monday is 0, Sunday is 6
        current_hour = current_time.hour

        # Only consider entries on Monday during US trading hours
        is_monday_us_hours = (
            current_weekday == 0 and
            self.monday_entry_hour_start <= current_hour < self.monday_entry_hour_end
        )

        if not is_monday_us_hours:
            return None

        # Check for sufficient history to calculate weekend move (Fri 12:00 UTC to Sun 12:00 UTC)
        # We need data from Sunday 12:00 UTC (48 bars ago) to Friday 12:00 UTC (96 bars ago)
        required_history = self.weekend_lookback_bars * 2 # For Fri start
        if len(closes) < required_history:
            return None

        # Calculate weekend price move (Friday 12:00 UTC to Sunday 12:00 UTC)
        # Assuming current bar is Monday, 12:00 UTC.
        # Sunday 12:00 UTC was 24 bars ago. Friday 12:00 UTC was 24 + 48 = 72 bars ago.
        # This needs to be adjusted based on the current bar's timestamp.
        # Let's simplify: look at the close 24 bars ago (Sunday 12:00 UTC) vs 72 bars ago (Friday 12:00 UTC)
        # This assumes the strategy is running on Monday 12:00 UTC
        
        # To get the precise weekend close (Sunday 12:00 UTC) and open (Friday 12:00 UTC)
        # We need to find the closes at specific past timestamps.
        # This is hard to do with just `closes` array which is relative to current bar.
        # A simpler approach: calculate the move from the close of Friday (approx 72 bars ago)
        # to the close of Sunday (approx 24 bars ago).
        
        # Let's define the "weekend" as the period from Friday 12:00 UTC to Sunday 12:00 UTC.
        # If current bar is Monday 12:00 UTC, then:
        # Sunday 12:00 UTC is `closes[-25]` (index for 24 bars ago, if current is -1)
        # Friday 12:00 UTC is `closes[-73]` (index for 72 bars ago)
        
        # This needs to be more robust. Let's find the closes based on time.
        # We need to find the close of the bar that occurred at Sunday 12:00 UTC and Friday 12:00 UTC.
        
        # For simplicity and given the `closes` array is ordered oldest first:
        # Let's assume `closes[-1]` is the current bar's close.
        # `closes[-25]` (24 bars ago) is approximately Sunday 12:00 UTC (if current is Monday 12:00 UTC)
        # `closes[-73]` (72 bars ago) is approximately Friday 12:00 UTC
        
        # This is fragile due to bar skips/misses.
        # Let's use a fixed offset from the current bar's index.
        # The weekend move is defined by the close 24 bars ago (Sunday close)
        # and the close 72 bars ago (Friday close).
        
        friday_close = closes[-72] # Roughly Friday 12:00 UTC
        sunday_close = closes[-24] # Roughly Sunday 12:00 UTC
        
        weekend_move_pct = ((sunday_close - friday_close) / friday_close) * 100

        # Calculate EMA for trend confirmation
        ema_value = self._ema(closes, self.ema_period)
        if ema_value is None:
            return None

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Long entry logic: Weekend was down, and Monday is rebuilding above EMA
        if weekend_move_pct < -0.5: # Significant weekend drop
            if ctx.bar.close > ema_value:
                signal_direction = "long"
                confidence = 0.7 + min(0.3, abs(weekend_move_pct) / 2.0) # Higher confidence for bigger drops
                reason = "weekend_drop_monday_rebuild_above_ema"
        # Short entry logic: Weekend was up, and Monday is reversing below EMA
        elif weekend_move_pct > 0.5: # Significant weekend rally
            if ctx.bar.close < ema_value:
                signal_direction = "short"
                confidence = 0.7 + min(0.3, abs(weekend_move_pct) / 2.0)
                reason = "weekend_rally_monday_reverse_below_ema"

        if signal_direction and not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "weekend_move_pct": round(weekend_move_pct, 2),
                    "current_price": ctx.bar.close,
                    "ema_value": round(ema_value, 2),
                    "current_hour": current_hour,
                    "current_weekday": current_weekday
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to enforce cooldown after any closure
        self.last_trade_bar = ctx.bar_index
        # You could also use this to adapt parameters, e.g., if many losses, increase cooldown
        pass