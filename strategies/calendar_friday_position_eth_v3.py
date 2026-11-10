from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FridayPositioningETH(Strategy):
    METADATA = {
        "name": "Friday Positioning ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hours hold
        "warmup_bars": 30,
        "required_features": [], # No external features used for this specific calendar strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_signal_bar = -100 # Cooldown period after a signal
        self.entry_cooldown_bars = 12 # 12 hours cooldown after an entry
        self.last_friday_signal_bar = -100
        self.friday_signal_cooldown_bars = 48 # Cooldown for specific Friday signal

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1) # Need one more for diff
        volumes = ctx.volumes(self.METADATA["warmup_bars"] + 1)

        if len(closes) < self.METADATA["warmup_bars"] + 1:
            return None

        # Cooldown after any recent signal
        if ctx.bar_index < self.last_signal_bar + self.entry_cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Friday is 4, Sunday is 6

        # Strategy: Look for decisive moves late Friday UTC
        # Friday (4) UTC, hours 18-23 (last 6 hours)
        is_friday_late = (current_weekday == 4 and current_hour >= 18)

        # Only trigger once per Friday period
        if is_friday_late and ctx.bar_index < self.last_friday_signal_bar + self.friday_signal_cooldown_bars:
            return None

        # Check for decisive move using a short-term SMA and volume
        # We need at least 6 bars for a 6-hour decisive move check
        if len(closes) < 6 or len(volumes) < 6:
            return None

        # Calculate 6-hour price change
        price_change_6h = closes[-1] - closes[-6]
        
        # Calculate 6-hour average volume
        avg_volume_6h = self._sma(volumes, 6)

        # Check for significant price change and higher than average volume
        # Use a short SMA (e.g., 6-bar) to confirm direction
        sma_6 = self._sma(closes, 6)
        if sma_6 is None:
            return None

        # Decide on decisiveness thresholds
        # Let's say 0.5% move in 6 hours for decisiveness, and 20% higher volume
        decisive_price_threshold_pct = 0.005 # 0.5% move
        volume_confirm_factor = 1.2 # 20% higher volume than 6-period average

        # Calculate a longer-term average volume for comparison (e.g., 24-hour)
        if len(volumes) < 24:
            return None
        long_avg_volume = self._sma(volumes, 24)
        if long_avg_volume is None:
            return None
        
        current_bar_volume = volumes[-1]

        signal_confidence = 0.0
        signal_direction = "hold"
        reason = "no_signal"
        
        # Long condition: Price moved up decisively, confirmed by volume, and price is above short SMA
        if (price_change_6h / closes[-6]) > decisive_price_threshold_pct and \
           current_bar_volume > long_avg_volume * volume_confirm_factor and \
           closes[-1] > sma_6 and \
           is_friday_late and \
           not ctx.has_position():
            
            signal_direction = "long"
            signal_confidence = 0.7 + ctx.rng.uniform(0.0, 0.1) # Add a touch of randomness for exploration
            reason = "friday_late_decisive_long"
            self.last_signal_bar = ctx.bar_index
            self.last_friday_signal_bar = ctx.bar_index

        # Short condition: Price moved down decisively, confirmed by volume, and price is below short SMA
        elif (price_change_6h / closes[-6]) < -decisive_price_threshold_pct and \
             current_bar_volume > long_avg_volume * volume_confirm_factor and \
             closes[-1] < sma_6 and \
             is_friday_late and \
             not ctx.has_position():
            
            signal_direction = "short"
            signal_confidence = 0.7 + ctx.rng.uniform(0.0, 0.1)
            reason = "friday_late_decisive_short"
            self.last_signal_bar = ctx.bar_index
            self.last_friday_signal_bar = ctx.bar_index

        # Exit condition: If holding a position and it's Sunday (0-2 UTC) or Monday morning (0-2 UTC), consider exiting
        # This allows the position to run through late Friday and Saturday.
        if ctx.has_position():
            # Exit if it's Sunday or Monday early hours (UTC) to avoid Monday open gap risk
            if (current_weekday == 6 and current_hour >= 0) or \
               (current_weekday == 0 and current_hour < 3): # Exit before 3 AM Monday UTC
                
                # Only exit if the position has been open for at least 6 hours
                if ctx.bar_index >= self.last_signal_bar + 6:
                    signal_direction = "flat"
                    signal_confidence = 0.8
                    reason = "friday_position_weekend_exit"
                    # No update to last_signal_bar here, as it's an exit, not an entry
                    # self.last_signal_bar = ctx.bar_index # Do not reset cooldown on exit

        if signal_direction != "hold":
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "current_weekday": current_weekday,
                    "price_change_6h_pct": (price_change_6h / closes[-6]) * 100,
                    "current_volume": current_bar_volume,
                    "long_avg_volume": long_avg_volume,
                    "sma_6": sma_6,
                    "current_close": closes[-1]
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: You could adapt self.entry_cooldown_bars based on position outcome
        # For this strategy, a fixed cooldown is preferred to limit trade frequency.
        pass