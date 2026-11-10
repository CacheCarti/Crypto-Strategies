from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeeklyTransitionSOL(Strategy):
    METADATA = {
        "name": "Weekly Transition SOLUSDC",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2-3 hours, but can be shorter if Monday close is hit
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_period = 20
        self.short_period = 5
        self.volume_period = 10
        self.entry_cooldown = 6 # Cooldown for 6 hours after a trade
        self.last_signal_bar = -self.entry_cooldown

        self.last_sunday_close = None
        self.transition_detected = False
        self.transition_direction = None
        self.transition_bar_index = -1

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_period)
        volumes = ctx.volumes(self.volume_period)

        if len(closes) < self.warmup_bars or len(volumes) < self.volume_period:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Reset transition detection at the start of a new week (Sunday UTC 00:00)
        if current_weekday == 6 and current_hour == 0 and self.last_sunday_close is None:
            self.last_sunday_close = ctx.bar.close
            self.transition_detected = False
            self.transition_direction = None
            self.transition_bar_index = -1
            return None # Wait for the first bar after Sunday open

        # If it's Monday 21:00 UTC or later, close any open position
        if current_weekday == 0 and current_hour >= 21 and ctx.has_position():
            return ctx.signal("flat", confidence=0.8, metadata={"reason": "monday_close_deadline", "current_hour": current_hour})

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar < self.entry_cooldown:
            return None

        # Detect the first decisive move after Sunday UTC 00:00
        # Only check on Monday (weekday 0) before 21:00 UTC
        if current_weekday == 0 and current_hour < 21 and self.last_sunday_close is not None and not self.transition_detected:
            # Check for a significant move relative to Sunday's close
            price_change_pct = (ctx.bar.close - self.last_sunday_close) / self.last_sunday_close * 100
            
            # Volume confirmation: current volume above average
            avg_volume = self._sma(volumes, self.volume_period)
            if avg_volume is None:
                return None
            
            volume_zscore = self._zscore(volumes, self.volume_period)

            # Define a threshold for a "decisive" move (e.g., 0.5% - 1%)
            decisive_move_threshold = 0.75 # in percent
            volume_zscore_threshold = 0.5 # above 0.5 std dev average volume

            if price_change_pct > decisive_move_threshold and volume_zscore > volume_zscore_threshold:
                self.transition_detected = True
                self.transition_direction = "long"
                self.transition_bar_index = ctx.bar_index
                
                # Avoid single spike bar: check if previous bar also moved in the same direction or was neutral
                # This is a simplification; a more robust check would involve multiple bars
                if len(closes) > 1 and (closes[-2] - self.last_sunday_close) / self.last_sunday_close * 100 < decisive_move_threshold / 2:
                    # Previous bar was not a strong move, so this might be a spike. Wait one more bar.
                    self.transition_detected = False
                    return None

            elif price_change_pct < -decisive_move_threshold and volume_zscore > volume_zscore_threshold:
                self.transition_detected = True
                self.transition_direction = "short"
                self.transition_bar_index = ctx.bar_index

                if len(closes) > 1 and (closes[-2] - self.last_sunday_close) / self.last_sunday_close * 100 > -decisive_move_threshold / 2:
                    self.transition_detected = False
                    return None

        # Execute trade if transition detected and not in a position
        if self.transition_detected and self.transition_direction and not ctx.has_position():
            # Only trade if the current bar is the transition bar or the one immediately after
            if ctx.bar_index == self.transition_bar_index or ctx.bar_index == self.transition_bar_index + 1:
                signal_direction = self.transition_direction
                confidence = 0.75 # High confidence for a detected transition
                self.last_signal_bar = ctx.bar_index
                
                # Reset Sunday close to prevent re-triggering this week
                self.last_sunday_close = None

                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": f"weekly_transition_follow_{signal_direction}",
                        "price_change_pct": price_change_pct,
                        "volume_zscore": volume_zscore,
                        "last_sunday_close": self.last_sunday_close,
                        "current_price": ctx.bar.close
                    }
                )
        
        # If we are in a position, just hold unless it's Monday 21:00 UTC
        if ctx.has_position():
            # Check if it's Monday and past 21:00 UTC
            if current_weekday == 0 and current_hour >= 21:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "monday_end_of_day_exit", "current_hour": current_hour})
            return ctx.signal("hold")

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset transition flags after a position closes for any reason
        self.transition_detected = False
        self.transition_direction = None
        self.last_sunday_close = None # Ensure it gets re-evaluated next Sunday
        pass