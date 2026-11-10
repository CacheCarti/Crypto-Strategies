from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class MondayRebound(Strategy):
    METADATA = {
        "name": "Monday Rebound",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours intended hold
        "warmup_bars": 72, # Need enough data for weekend high/low (48 bars) + cooldown
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_signal_bar = -self.METADATA["warmup_bars"]
        self.cooldown_bars = 24 # Cooldown for 24 hours after a trade
        self.weekend_high = -1.0
        self.weekend_low = float('inf')
        self.weekend_close = -1.0
        self.last_monday_signal_bar = -1
        self.last_monday_trade_direction = None

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        highs = ctx.highs(self.METADATA["warmup_bars"] + 1)
        lows = ctx.lows(self.METADATA["warmup_bars"] + 1)
        volumes = ctx.volumes(self.METADATA["warmup_bars"] + 1)

        if len(closes) <= self.METADATA["warmup_bars"]:
            return None

        current_timestamp = ctx.bar.timestamp
        current_day_of_week = current_timestamp.weekday() # Monday is 0, Sunday is 6
        current_hour = current_timestamp.hour

        # Update weekend high/low/close
        if current_day_of_week == 4 and current_hour == 23: # Friday 23:00 UTC
            self.weekend_close = ctx.bar.close
            self.weekend_high = ctx.bar.high
            self.weekend_low = ctx.bar.low
        elif current_day_of_week >= 5 or (current_day_of_week == 0 and current_hour < 0): # Saturday, Sunday
            self.weekend_high = max(self.weekend_high, ctx.bar.high)
            self.weekend_low = min(self.weekend_low, ctx.bar.low)
        elif current_day_of_week == 0 and current_hour >= 0: # Monday, reset weekend data
            # Only reset if we're past the weekend
            if self.weekend_close != -1.0: # Check if weekend data was actually recorded
                self.weekend_high = -1.0
                self.weekend_low = float('inf')
                self.weekend_close = -1.0

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # --- Monday Rebound Logic ---
        # Trigger only on Monday, during US trading hours (e.g., 14:00-22:00 UTC)
        if current_day_of_week == 0 and 14 <= current_hour <= 22: # Monday between 14:00 UTC and 22:00 UTC
            # Ensure we have valid weekend data
            if self.weekend_close == -1.0:
                return None

            # Only allow one trade per Monday event
            if self.last_monday_signal_bar == ctx.bar_index -1 and ctx.has_position():
                return None

            # Calculate weekend price change
            weekend_change_pct = ((ctx.bar.close - self.weekend_close) / self.weekend_close) * 100 if self.weekend_close > 0 else 0

            # Simple moving average for trend confirmation
            fast_ma = self._sma(closes, 12) # 12-hour SMA
            slow_ma = self._sma(closes, 24) # 24-hour SMA (1 day)
            if fast_ma is None or slow_ma is None:
                return None

            # Entry conditions
            signal_direction = None
            confidence = 0.0
            reason = ""
            current_price = ctx.bar.close

            # Long entry: Weekend sell-off, Monday price is rebuilding above fast MA, and fast MA > slow MA
            if weekend_change_pct < -1.0 and current_price > fast_ma and fast_ma > slow_ma:
                # Check for confirmation: current price is moving up from weekend low
                if current_price > self.weekend_low * 1.005: # 0.5% above weekend low
                    signal_direction = "long"
                    confidence = 0.75
                    reason = "monday_rebound_long_after_weekend_selloff"
            # Short entry: Weekend rally, Monday price is rebuilding below fast MA, and fast MA < slow MA
            elif weekend_change_pct > 1.0 and current_price < fast_ma and fast_ma < slow_ma:
                # Check for confirmation: current price is moving down from weekend high
                if current_price < self.weekend_high * 0.995: # 0.5% below weekend high
                    signal_direction = "short"
                    confidence = 0.75
                    reason = "monday_rebound_short_after_weekend_rally"

            if signal_direction:
                # Avoid flipping too quickly on the same Monday event
                if self.last_monday_signal_bar == current_day_of_week and self.last_monday_trade_direction == signal_direction:
                    return None

                self.last_signal_bar = ctx.bar_index
                self.last_monday_signal_bar = current_day_of_week
                self.last_monday_trade_direction = signal_direction

                if not ctx.has_position():
                    return ctx.signal(
                        signal_direction,
                        confidence=confidence,
                        metadata={
                            "reason": reason,
                            "weekend_change_pct": round(weekend_change_pct, 2),
                            "current_price": round(current_price, 2),
                            "fast_ma": round(fast_ma, 2),
                            "slow_ma": round(slow_ma, 2),
                            "current_hour": current_hour,
                            "cooldown_bars_set": self.cooldown_bars
                        }
                    )
                elif ctx.position_direction() != signal_direction:
                    # Flip position if opposite signal
                    return ctx.signal(
                        signal_direction,
                        confidence=confidence,
                        metadata={
                            "reason": f"flip_{reason}",
                            "weekend_change_pct": round(weekend_change_pct, 2),
                            "current_price": round(current_price, 2),
                            "fast_ma": round(fast_ma, 2),
                            "slow_ma": round(slow_ma, 2),
                            "current_hour": current_hour,
                            "cooldown_bars_set": self.cooldown_bars
                        }
                    )
        
        # Exit logic: if we are in a position and it's no longer Monday trading hours, or a new Monday is starting.
        # This ensures we don't hold the position for too long beyond the event.
        if ctx.has_position() and (current_day_of_week != 0 or current_hour > 23):
             # Or if we've held for a significant duration, close to prevent over-holding
            if ctx.bar_index - self.last_signal_bar > (self.METADATA["declared_hold_seconds"] / 3600) * 1.5: # 1.5x declared hold duration
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_based_exit_monday_over",
                        "current_day": current_day_of_week,
                        "current_hour": current_hour,
                        "bars_held": ctx.bar_index - self.last_signal_bar
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last signal bar to enforce cooldown after any position close
        self.last_signal_bar = ctx.bar_index
        # We can also adapt cooldown_bars based on performance if needed, but keeping it fixed for simplicity.
        pass