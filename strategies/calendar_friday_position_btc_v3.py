from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FridayPositioningBTC(Strategy):
    METADATA = {
        "name": "Friday Positioning BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hours hold
        "warmup_bars": 30,
        "required_features": [], # No external features needed for this calendar strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_signal_bar = -100 # Cooldown period
        self.last_friday_trigger_bar = -100 # Ensure only one signal per Friday window

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        volumes = ctx.volumes(30)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown period after a signal
        if ctx.bar_index <= self.last_signal_bar + 10: # Wait 10 bars (10 hours) after any signal
            return None

        # Identify Friday UTC 20:00 to 23:00 (last 4 hours of Friday)
        # 0 = Monday, 4 = Friday, 6 = Sunday
        is_friday = ctx.bar.timestamp.weekday() == 4 # Friday
        is_late_friday_utc = 20 <= ctx.bar.timestamp.hour <= 23 # 20:00 to 23:00 UTC

        if is_friday and is_late_friday_utc:
            # Prevent multiple signals within the same Friday window
            if ctx.bar_index <= self.last_friday_trigger_bar + 4: # Only trigger once every 4 hours within the window
                return None

            # Check for decisive move in the last 6 hours
            # We need at least 6 bars for this
            if len(closes) < 6 or len(volumes) < 6:
                return None

            # Calculate average volume for confirmation
            avg_volume_6h = self._sma(volumes, 6)
            if avg_volume_6h is None:
                return None

            current_close = closes[-1]
            six_hours_ago_close = closes[-6]
            
            # Determine "decisive" move: a significant price change confirmed by above-average volume
            price_change_pct = (current_close - six_hours_ago_close) / six_hours_ago_close * 100
            
            # Use a longer-term average volume for comparison
            avg_volume_20h = self._sma(volumes, 20)
            if avg_volume_20h is None:
                return None

            volume_confirmation = volumes[-1] > avg_volume_20h * 1.2 # Current volume 20% higher than 20-hour avg
            
            signal_fired = False
            signal_direction = "hold"
            confidence = 0.0
            reason = ""
            
            # Long signal: Significant upward move with volume confirmation
            if price_change_pct > 0.5 and volume_confirmation: # 0.5% up in 6 hours
                signal_direction = "long"
                confidence = min(1.0, 0.4 + (price_change_pct / 1.0) * 0.6) # Scale confidence
                reason = "friday_positioning_long_decisive_up_move"
                signal_fired = True

            # Short signal: Significant downward move with volume confirmation
            elif price_change_pct < -0.5 and volume_confirmation: # 0.5% down in 6 hours
                signal_direction = "short"
                confidence = min(1.0, 0.4 + (abs(price_change_pct) / 1.0) * 0.6) # Scale confidence
                reason = "friday_positioning_short_decisive_down_move"
                signal_fired = True
            
            if signal_fired:
                self.last_signal_bar = ctx.bar_index
                self.last_friday_trigger_bar = ctx.bar_index
                
                # If already in a position, flip or hold
                if ctx.has_position():
                    if ctx.position_direction() == signal_direction:
                        return ctx.signal("hold") # Stay in position
                    else:
                        # Flip position
                        return ctx.signal(
                            signal_direction,
                            confidence=confidence,
                            metadata={
                                "reason": f"flip_from_{ctx.position_direction()}_to_{reason}",
                                "price_change_pct": round(price_change_pct, 2),
                                "current_volume": round(volumes[-1], 2),
                                "avg_volume_20h": round(avg_volume_20h, 2),
                                "timestamp_utc": ctx.bar.timestamp.isoformat(),
                            }
                        )
                else:
                    # New position
                    return ctx.signal(
                        signal_direction,
                        confidence=confidence,
                        metadata={
                            "reason": reason,
                            "price_change_pct": round(price_change_pct, 2),
                            "current_volume": round(volumes[-1], 2),
                            "avg_volume_20h": round(avg_volume_20h, 2),
                            "timestamp_utc": ctx.bar.timestamp.isoformat(),
                        }
                    )
        
        # If a position is open and it's Sunday 00:00 UTC, consider exiting
        # This acts as a time-based exit for the Friday positioning trade
        is_sunday_midnight = ctx.bar.timestamp.weekday() == 6 and ctx.bar.timestamp.hour == 0

        if ctx.has_position() and is_sunday_midnight:
            self.last_signal_bar = ctx.bar_index # Apply cooldown after exit
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": "sunday_midnight_exit_calendar_trade",
                    "entry_price": ctx.position["entry_price"],
                    "current_price": ctx.bar.close,
                    "timestamp_utc": ctx.bar.timestamp.isoformat(),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis, reset any state if necessary
        pass