from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionRecovery(Strategy):
    METADATA = {
        "name": "SOL Session Recovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 24, # Need enough bars to detect price drops
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.drop_threshold_pct = 2.0 # % drop required for a flush
        self.recovery_threshold_pct = 0.5 # % recovery after drop to confirm stabilization
        self.cooldown_bars = 6
        self.last_trade_bar = -self.cooldown_bars

        self.flush_start_price = 0.0
        self.flush_low_price = 0.0
        self.flush_high_price = 0.0
        self.flush_bar_index = -1
        self.flush_detected = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(24) # Look back 24 hours
        highs = ctx.highs(24)
        lows = ctx.lows(24)
        current_close = ctx.bar.close
        current_timestamp = ctx.bar.timestamp

        if len(closes) < self.METADATA["warmup_bars"]:
            return None
        
        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # --- Flush Detection (UTC 0-8) ---
        # Look for a significant drop within the UTC 0-8 window
        if 0 <= current_timestamp.hour < 8:
            # Find the highest price in the last 6 hours (or less if not enough data)
            lookback_period = min(len(closes), 6)
            recent_high = max(highs[-lookback_period:])
            
            # Check for a sharp drop from recent high to current low
            if recent_high > 0 and (recent_high - ctx.bar.low) / recent_high * 100 >= self.drop_threshold_pct:
                self.flush_detected = True
                self.flush_start_price = recent_high
                self.flush_low_price = ctx.bar.low
                self.flush_bar_index = ctx.bar_index
                self.flush_high_price = ctx.bar.high # Track high of the drop bar for recovery
                # print(f"Flush detected at {current_timestamp}: High={recent_high}, Low={ctx.bar.low}")

        # --- Recovery Entry (UTC 8-12) ---
        # If flush detected and we are in the recovery window
        if self.flush_detected and 8 <= current_timestamp.hour < 12:
            # Ensure we are past the flush bar
            if ctx.bar_index <= self.flush_bar_index:
                return None

            # Check if current price is recovering from the flush low
            # And if the current bar is holding above the flush low
            if current_close > self.flush_low_price and \
               (current_close - self.flush_low_price) / self.flush_low_price * 100 >= self.recovery_threshold_pct and \
               ctx.bar.low >= self.flush_low_price * 0.999: # Stabilizing above the panic low (allow small wick below)

                # Entry condition: Buy if not already in position
                if not ctx.has_position():
                    # Calculate potential retracement target for TP
                    # Target 38.2% retracement of the drop
                    drop_size = self.flush_start_price - self.flush_low_price
                    target_price = self.flush_low_price + (drop_size * 0.382)
                    
                    # Ensure TP is reasonable and above current price
                    take_profit_bps = max(self.METADATA["declared_tp_bps"], (target_price / current_close - 1.0) * 10000)
                    
                    self.last_trade_bar = ctx.bar_index
                    self.flush_detected = False # Reset flush state after entry
                    
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=take_profit_bps,
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "session_recovery_long",
                            "flush_start_price": self.flush_start_price,
                            "flush_low_price": self.flush_low_price,
                            "recovery_price": current_close,
                            "time_of_day": current_timestamp.hour
                        }
                    )
        
        # Reset flush detection outside the detection/recovery windows
        if current_timestamp.hour >= 12 and self.flush_detected:
            self.flush_detected = False

        # --- Exit by UTC 21 ---
        if ctx.has_position() and ctx.position_direction() == "long":
            if current_timestamp.hour >= 21:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_based_exit_eod",
                        "exit_hour": current_timestamp.hour,
                        "current_price": current_close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset flush detection on position close to ensure a clean state for the next trade
        self.flush_detected = False
        self.flush_start_price = 0.0
        self.flush_low_price = 0.0
        self.flush_bar_index = -1
        self.flush_high_price = 0.0
        # No need to update last_trade_bar here, it's updated on signal generation