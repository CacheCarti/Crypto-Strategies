from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BTCSessionDrift(Strategy):
    METADATA = {
        "name": "BTC Session Drift",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # ~2 hours, to capture next session
        "warmup_bars": 48, # For 24-hour range and median
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_start_hour = 0  # UTC 00:00
        self.overnight_end_hour = 6    # UTC 06:00 (exclusive)
        self.entry_cooldown_bars = 6   # Wait 6 hours after entry/exit
        self.last_signal_bar_index = -self.entry_cooldown_bars * 2 # Initialize far in past

        self.range_period = 24 # For median range calculation
        self.min_drift_factor = 0.5 # Drift must be at least 0.5x median range
        self.max_drift_factor = 2.0 # Avoid already exhausted moves (2x median range)
        self.spike_threshold_factor = 0.6 # Single bar should not be more than X% of drift
        self.max_hold_bars = 10 # Max hold 10 hours if no TP/SL

        # State to track session moves and avoid re-entry
        self.session_drift_start_price = None
        self.session_drift_direction = None
        self.session_drift_high = -math.inf
        self.session_drift_low = math.inf
        self.last_overnight_session_day = -1 # To ensure only one entry per day

    def _median(self, values):
        s_values = sorted(values)
        mid = len(s_values) // 2
        if len(s_values) % 2 == 0:
            return (s_values[mid - 1] + s_values[mid]) / 2
        else:
            return s_values[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period + 1)
        highs = ctx.highs(self.range_period + 1)
        lows = ctx.lows(self.range_period + 1)

        if len(closes) < self.range_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Calculate median daily range for the last N days
        daily_ranges = []
        for i in range(1, self.range_period + 1):
            if i < len(highs) and i < len(lows):
                daily_ranges.append(highs[-i] - lows[-i])
        
        median_range = self._median(daily_ranges) if daily_ranges else 0.001

        # --- Session Drift Logic ---
        # Detect start of overnight session
        if current_hour == self.overnight_start_hour and current_day != self.last_overnight_session_day:
            self.session_drift_start_price = ctx.bar.open
            self.session_drift_direction = None
            self.session_drift_high = ctx.bar.high
            self.session_drift_low = ctx.bar.low
            self.last_overnight_session_day = current_day # Mark that we've started tracking for this day
        
        # Update session high/low during overnight session
        if self.session_drift_start_price is not None and self.overnight_start_hour <= current_hour < self.overnight_end_hour:
            self.session_drift_high = max(self.session_drift_high, ctx.bar.high)
            self.session_drift_low = min(self.session_drift_low, ctx.bar.low)
        
        # Check for signal at the end of the overnight session, or shortly after
        if current_hour == self.overnight_end_hour or current_hour == self.overnight_end_hour + 1: # Check at 06:00 UTC or 07:00 UTC
            if self.session_drift_start_price is not None and self.session_drift_direction is None: # Only signal once per session
                
                drift_magnitude = ctx.bar.close - self.session_drift_start_price
                abs_drift = abs(drift_magnitude)

                # Check if drift is decisive but not exhausted
                if (self.min_drift_factor * median_range < abs_drift < self.max_drift_factor * median_range):
                    
                    # Prevent single-bar spikes from triggering
                    # If the last bar's move itself constitutes a large portion of the total drift, it might be a spike
                    last_bar_move = abs(ctx.bar.close - ctx.bar.open)
                    if last_bar_move / abs_drift < self.spike_threshold_factor:

                        if drift_magnitude > 0: # Bullish drift
                            self.session_drift_direction = "long"
                        elif drift_magnitude < 0: # Bearish drift
                            self.session_drift_direction = "short"
                        
                        if self.session_drift_direction:
                            # Cooldown check
                            if ctx.bar_index - self.last_signal_bar_index < self.entry_cooldown_bars:
                                return None

                            # Signal to follow the drift
                            if not ctx.has_position():
                                confidence = min(1.0, abs_drift / median_range) # Confidence scales with drift strength
                                signal_direction = self.session_drift_direction
                                self.last_signal_bar_index = ctx.bar_index
                                
                                return ctx.signal(
                                    signal_direction,
                                    confidence=confidence,
                                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                                    take_profit_bps=self.METADATA["declared_tp_bps"],
                                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                                    metadata={
                                        "reason": f"overnight_session_drift_{signal_direction}",
                                        "drift_magnitude": round(abs_drift, 2),
                                        "median_range": round(median_range, 2),
                                        "start_price": round(self.session_drift_start_price, 2),
                                        "end_price": round(ctx.bar.close, 2),
                                        "current_hour": current_hour
                                    }
                                )
                # Reset drift tracking after evaluation for the day
                self.session_drift_start_price = None
                self.session_drift_direction = None
                self.session_drift_high = -math.inf
                self.session_drift_low = math.inf

        # --- Position Management ---
        if ctx.has_position():
            # Exit if held too long (before next US session, roughly 13:00 UTC)
            if ctx.bar_index - ctx.position["entry_bar_index"] >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_time_reached",
                        "bars_held": ctx.bar_index - ctx.position["entry_bar_index"],
                        "current_hour": current_hour
                    }
                )
            
            # Additional structure stop: if price moves significantly against the drift direction within the trade
            # This is a simpler trailing stop that doesn't track peak profit
            if ctx.position_direction() == "long" and ctx.bar.close < ctx.position["entry_price"] - (0.5 * median_range):
                 return ctx.signal("flat", confidence=0.6, metadata={"reason": "structure_stop_long", "price": ctx.bar.close, "entry": ctx.position["entry_price"], "median_range": round(median_range, 2)})
            if ctx.position_direction() == "short" and ctx.bar.close > ctx.position["entry_price"] + (0.5 * median_range):
                 return ctx.signal("flat", confidence=0.6, metadata={"reason": "structure_stop_short", "price": ctx.bar.close, "entry": ctx.position["entry_price"], "median_range": round(median_range, 2)})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close as well, to prevent immediate re-entry after a quick stop-out
        self.last_signal_bar_index = ctx.bar_index
        # Log for insights
        # ctx.log(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps, held {position['bars_held']} bars.")