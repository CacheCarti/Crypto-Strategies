from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityThreeDriveSOL(Strategy):
    METADATA = {
        "name": "VolatilityThreeDriveSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 30
        self.momentum_period = 5
        self.min_drive_strength = 0.005 # 0.5% move for a drive
        self.cooldown_bars = 10
        self.last_signal_bar = -self.cooldown_bars * 2 # Initialize to allow immediate trading

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.lookback_period + 1:
            return None

        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Calculate short-term momentum
        momentum = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        avg_momentum = self._sma(momentum[-self.momentum_period:], self.momentum_period)
        
        if avg_momentum is None:
            return None

        # Detect directional pushes and their failures
        # A "drive" is a sustained move in one direction
        # We look for three drives in the same direction, with the third failing
        
        # Look for recent swing points (local min/max)
        # This is a simplified approach to detect turning points
        is_uptrend = closes[-1] > closes[-5] and closes[-1] > closes[-10]
        is_downtrend = closes[-1] < closes[-5] and closes[-1] < closes[-10]
        
        current_price = ctx.bar.close
        
        # Simplified three-drive detection
        # We look for sequences of increasing/decreasing highs/lows
        # and then a reversal attempt with weakening momentum
        
        # Check for potential long setup (three drives down, failure to make new low)
        if not ctx.has_position() and ctx.regime != "crisis" and ctx.regime != "volatile":
            # Look for 3 lower lows in a row, with the last one being weak or failing to hold
            # Simple heuristic: three consecutive bars closing lower, then a bounce attempt
            
            # Drive 1: c[t-3] > c[t-2]
            # Drive 2: c[t-2] > c[t-1]
            # Drive 3: c[t-1] > c[t] (current bar's close is lower than prev)
            # Failure: current bar's low is not significantly lower than prev low, and momentum is weakening
            
            # Simplified "three drives down" detection
            if (closes[-4] > closes[-3] and
                closes[-3] > closes[-2] and
                closes[-2] > closes[-1] and
                (closes[-1] - closes[-2]) / closes[-2] < -self.min_drive_strength and # third drive is significant
                avg_momentum > -0.0001 and # momentum is no longer strongly negative
                current_price > closes[-1] # current bar is trying to recover
            ):
                # Ensure the last drive was a clear push down, but now failing to extend
                if (lows[-1] < lows[-2] and # new low made
                    (current_price - lows[-1]) / lows[-1] > 0.002 and # bounced off low
                    (closes[-1] - closes[-2]) / closes[-2] < -0.005 # previous bar was a clear down
                ):
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "three_drives_down_failure_long",
                            "avg_momentum": avg_momentum,
                            "current_price": current_price,
                            "prev_close": closes[-1],
                            "regime": ctx.regime
                        }
                    )

        # Check for potential short setup (three drives up, failure to make new high)
        if not ctx.has_position() and ctx.regime != "crisis" and ctx.regime != "volatile":
            # Look for 3 higher highs in a row, with the last one being weak or failing to hold
            if (closes[-4] < closes[-3] and
                closes[-3] < closes[-2] and
                closes[-2] < closes[-1] and
                (closes[-1] - closes[-2]) / closes[-2] > self.min_drive_strength and # third drive is significant
                avg_momentum < 0.0001 and # momentum is no longer strongly positive
                current_price < closes[-1] # current bar is trying to fall
            ):
                # Ensure the last drive was a clear push up, but now failing to extend
                if (highs[-1] > highs[-2] and # new high made
                    (highs[-1] - current_price) / highs[-1] > 0.002 and # rejected from high
                    (closes[-1] - closes[-2]) / closes[-2] > 0.005 # previous bar was a clear up
                ):
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "three_drives_up_failure_short",
                            "avg_momentum": avg_momentum,
                            "current_price": current_price,
                            "prev_close": closes[-1],
                            "regime": ctx.regime
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally log or adjust state based on position outcome
        pass