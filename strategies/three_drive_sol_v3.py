from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ThreeDriveExhaustion(Strategy):
    METADATA = {
        "name": "Three Drive Exhaustion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, 1-2 bars hold
        "warmup_bars": 60, # Need enough for 3 drives and indicators
        "required_features": [], # No specific features needed beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.drive_period = 5 # Bars to define a "drive"
        self.momentum_period = 3 # For momentum comparison
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_signal_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Look for 3 drives up (potential short)
        short_signal = self._check_three_drives(closes, highs, lows, "up")
        if short_signal and not ctx.has_position():
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=short_signal["confidence"],
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=short_signal["metadata"]
            )

        # Look for 3 drives down (potential long)
        long_signal = self._check_three_drives(closes, highs, lows, "down")
        if long_signal and not ctx.has_position():
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=long_signal["confidence"],
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=long_signal["metadata"]
            )
            
        # Exit logic
        if ctx.has_position():
            # Example: Exit if price moves significantly against us after entry
            entry_price = ctx.position["entry_price"]
            current_price = ctx.bar.close
            
            # Simple trailing stop logic (engine has its own, this is for strategy-specific exit)
            if ctx.position_direction() == "long":
                # If price drops 1.5x SL bps below entry
                if (entry_price - current_price) / entry_price * 10000 > self.METADATA["declared_sl_bps"] * 1.5:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "aggressive_stop_loss_hit", "entry_price": entry_price, "current_price": current_price})
                # Or if price reaches TP and then gives back a bit (engine will handle TP, this is an alternative)
                if (current_price - entry_price) / entry_price * 10000 >= self.METADATA["declared_tp_bps"]:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.8, metadata={"reason": "take_profit_reached", "entry_price": entry_price, "current_price": current_price})

            elif ctx.position_direction() == "short":
                # If price rises 1.5x SL bps above entry
                if (current_price - entry_price) / entry_price * 10000 > self.METADATA["declared_sl_bps"] * 1.5:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "aggressive_stop_loss_hit", "entry_price": entry_price, "current_price": current_price})
                # Or if price reaches TP
                if (entry_price - current_price) / entry_price * 10000 >= self.METADATA["declared_tp_bps"]:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.8, metadata={"reason": "take_profit_reached", "entry_price": entry_price, "current_price": current_price})
                    
        return None

    def _check_three_drives(self, closes, highs, lows, direction: str) -> Optional[Dict[str, Any]]:
        # A drive is a sustained move over self.drive_period bars
        # We need at least 3 * drive_period + momentum_period bars for analysis
        required_bars = 3 * self.drive_period + self.momentum_period + 1
        if len(closes) < required_bars:
            return None

        # Helper to calculate momentum (e.g., price change over a short period)
        def _get_momentum(vals, start_idx, end_idx):
            if start_idx < 0 or end_idx >= len(vals) or start_idx >= end_idx:
                return 0.0
            return vals[end_idx] - vals[start_idx]

        drives = []
        drive_moms = []

        # Iterate backward from current bar to find drives
        # A "drive" is a clear move of `drive_period` bars in one direction
        # Look for 3 distinct drives
        
        # Drive 1 (most recent)
        end_idx1 = len(closes) - 1
        start_idx1 = end_idx1 - self.drive_period
        if start_idx1 < 0: return None

        mom1 = _get_momentum(closes, start_idx1, end_idx1)
        
        # Drive 2
        end_idx2 = start_idx1 - 1
        start_idx2 = end_idx2 - self.drive_period
        if start_idx2 < 0: return None

        mom2 = _get_momentum(closes, start_idx2, end_idx2)

        # Drive 3
        end_idx3 = start_idx2 - 1
        start_idx3 = end_idx3 - self.drive_period
        if start_idx3 < 0: return None
        
        mom3 = _get_momentum(closes, start_idx3, end_idx3)

        # Check for consistent direction and weakening momentum
        if direction == "up":
            # All drives must be positive (upward)
            if not (mom1 > 0 and mom2 > 0 and mom3 > 0):
                return None
            # Momentum must be weakening: mom3 > mom2 > mom1
            # Allow for some tolerance in weakening momentum (e.g., mom1 < 0.9 * mom2)
            if not (mom3 > mom2 * 1.05 and mom2 > mom1 * 1.05): # Each drive must be significantly smaller than the previous
                return None

            # Additional check: price should be near recent highs for a short entry
            current_high = highs[-1]
            peak_of_drive1 = max(highs[start_idx1:end_idx1+1])
            if current_high < peak_of_drive1 * 0.995: # Current price close to the high of the last drive
                return None
            
            # Use Z-score of recent closes to confirm overextension
            zscore_val = self._zscore(closes, self.drive_period * 2)
            if zscore_val is None or zscore_val < 1.5: # Require significant overextension
                return None

            # Calculate the midpoint of the third drive for a potential take profit target
            midpoint_drive3 = (highs[end_idx3] + lows[start_idx3]) / 2 # Approx
            target_price = midpoint_drive3

            return {
                "confidence": 0.75,
                "metadata": {
                    "reason": "three_drive_up_exhaustion_short",
                    "drive1_mom": mom1,
                    "drive2_mom": mom2,
                    "drive3_mom": mom3,
                    "zscore": zscore_val,
                    "current_price": closes[-1],
                    "target_price": target_price
                }
            }

        elif direction == "down":
            # All drives must be negative (downward)
            if not (mom1 < 0 and mom2 < 0 and mom3 < 0):
                return None
            # Momentum must be weakening (absolute values): |mom3| > |mom2| > |mom1|
            if not (abs(mom3) > abs(mom2) * 1.05 and abs(mom2) > abs(mom1) * 1.05):
                return None

            # Additional check: price should be near recent lows for a long entry
            current_low = lows[-1]
            trough_of_drive1 = min(lows[start_idx1:end_idx1+1])
            if current_low > trough_of_drive1 * 1.005: # Current price close to the low of the last drive
                return None

            # Use Z-score of recent closes to confirm overextension
            zscore_val = self._zscore(closes, self.drive_period * 2)
            if zscore_val is None or zscore_val > -1.5: # Require significant underextension
                return None

            # Calculate the midpoint of the third drive for a potential take profit target
            midpoint_drive3 = (highs[start_idx3] + lows[end_idx3]) / 2 # Approx
            target_price = midpoint_drive3

            return {
                "confidence": 0.75,
                "metadata": {
                    "reason": "three_drive_down_exhaustion_long",
                    "drive1_mom": mom1,
                    "drive2_mom": mom2,
                    "drive3_mom": mom3,
                    "zscore": zscore_val,
                    "current_price": closes[-1],
                    "target_price": target_price
                }
            }
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_signal_bar to enforce cooldown after any position close
        self.last_signal_bar = ctx.bar_index
        # Log for diagnostics, if needed
        # ctx.log(f"Position closed: {position['direction']} return {position['return_bps']} bps, held for {position['bars_held']} bars.")
        pass