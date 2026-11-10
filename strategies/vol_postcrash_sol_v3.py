from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRecoveryDrift(Strategy):
    METADATA = {
        "name": "VolatilityRecoveryDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed
    }

    def initialize(self, ctx: BarContext) -> None:
        self.flush_period = 5
        self.stabilization_period = 3
        self.atr_period = 20
        self.vol_factor = 2.0 # How many ATRs for a flush
        self.recovery_lookback = 10 # Lookback for recovery trend
        self.min_recovery_slope = 0.001 # Minimum slope for recovery
        self.cooldown_bars = 10
        self.last_signal_bar = -self.cooldown_bars - 1 # Initialize to allow immediate signal

        # State to track for flush detection and cooldown
        self.is_flushing = False
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.flush_start_bar = -1

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close
        
        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Calculate ATR for volatility reference
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None:
            return None

        # --- Flush Detection ---
        # A flush is defined as a significant drop within a short period
        # Check if the market is currently flushing or has recently flushed
        
        # Condition 1: Check for a sharp drop
        # Compare current price to the highest price in the flush_period
        # If price drops by vol_factor * ATR, it's a flush
        if len(closes) >= self.flush_period:
            recent_high = max(highs[-self.flush_period:])
            
            if not self.is_flushing and (recent_high - current_price) > (self.vol_factor * atr):
                # Detected a new flush
                self.is_flushing = True
                self.flush_high = recent_high
                self.flush_low = current_price
                self.flush_start_bar = ctx.bar_index
                return None # Don't trade INTO the flush

            # If already flushing, update flush low
            if self.is_flushing and ctx.bar_index == self.flush_start_bar + 1: # Only update on the bar immediately after flush start
                self.flush_low = min(self.flush_low, current_price)


        # --- Stabilization and Recovery Detection ---
        if self.is_flushing:
            # Check if enough bars have passed since the flush started
            if ctx.bar_index < self.flush_start_bar + self.stabilization_period:
                return None # Still in the stabilization phase

            # Check if price has stopped falling significantly
            # We want price to be relatively stable or slightly rising after the flush
            # For stabilization, check if the price hasn't made new lows for 'stabilization_period' bars
            if len(lows) < self.stabilization_period:
                return None

            # Check for stabilization: no new lows below the flush_low within stabilization_period
            # And price has recovered slightly from the absolute low of the flush
            stabilized = True
            for i in range(1, self.stabilization_period + 1):
                if lows[-i] < self.flush_low * 0.995: # Allow slight dip, but not a full breakdown
                    stabilized = False
                    break
            
            # Check for recovery drift: price trending up over a short period after stabilization
            if stabilized and len(closes) >= self.recovery_lookback:
                # Calculate simple linear regression slope for recovery_lookback bars
                # This is a very simple approximation for slope detection
                # We expect closes[-1] > closes[-recovery_lookback]
                
                # Check for overall upward movement since flush low
                # Price should be above the flush low and showing some upward momentum
                if current_price > self.flush_low * 1.005: # Price has recovered at least 0.5% from flush low
                    # Simple slope approximation: average price of last half vs first half
                    recovery_closes = closes[-self.recovery_lookback:]
                    if len(recovery_closes) >= 2:
                        first_half_avg = sum(recovery_closes[:len(recovery_closes)//2]) / (len(recovery_closes)//2)
                        second_half_avg = sum(recovery_closes[len(recovery_closes)//2:]) / (len(recovery_closes) - len(recovery_closes)//2)
                        
                        if second_half_avg > first_half_avg * (1 + self.min_recovery_slope):
                            # Entry condition met
                            self.is_flushing = False # Reset flush state after entry
                            self.last_signal_bar = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.7,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "volatility_recovery_drift",
                                    "flush_start_bar": self.flush_start_bar,
                                    "flush_high": self.flush_high,
                                    "flush_low": self.flush_low,
                                    "current_price": current_price,
                                    "atr": atr,
                                    "recovery_slope_approx": (second_half_avg - first_half_avg) / first_half_avg,
                                }
                            )
            
            # If we've stabilized but no recovery, or if price breaks down further, reset flush state
            if ctx.bar_index > self.flush_start_bar + self.stabilization_period + self.recovery_lookback:
                self.is_flushing = False # Flush window expired without valid entry

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset flush state after a position closes, in case it was still active
        self.is_flushing = False
        # The cooldown is handled in on_bar by self.last_signal_bar