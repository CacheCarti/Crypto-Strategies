from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedRangeBreakoutReversion(Strategy):
    METADATA = {
        "name": "FailedRangeBreakoutReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 180.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 3600 * 2,  # Intended hold for 2 hours on 1h bars
        "warmup_bars": 50,  # Need enough bars for ATR and Range
    }

    def initialize(self, ctx: BarContext) -> None:
        self.range_period = 20  # Period for defining the range
        self.breakout_threshold_atr = 0.5  # Breakout must exceed this multiple of ATR
        self.reversion_threshold_atr = 0.25 # Price must revert this far back into range
        self.atr_period = 14
        self.cooldown_bars = 5  # Cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # State to detect transition for range breakout
        self.is_breaking_out = False
        self.breakout_direction = None # "long" or "short"
        self.breakout_price = None

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None
        
        current_price = ctx.bar.close

        # Calculate ATR
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr == 0:
            return None

        # Define the range
        range_high = max(highs[-self.range_period:-1]) # Exclude current bar for range
        range_low = min(lows[-self.range_period:-1])   # Exclude current bar for range
        
        if range_high <= range_low: # Avoid division by zero or invalid range
            return None

        # Check for active position
        if ctx.has_position():
            # If we are in a position, just hold. The engine manages SL/TP.
            # We could add logic here to flip direction if a stronger opposite signal appears,
            # but for this strategy, we'll let existing positions run their course.
            return None

        # --- Failed Breakout Detection ---

        # 1. Detect initial breakout attempt
        # Breakout to the upside
        if not self.is_breaking_out and current_price > range_high + self.breakout_threshold_atr * atr:
            self.is_breaking_out = True
            self.breakout_direction = "long"
            self.breakout_price = current_price
            return None # Wait for the failure to confirm

        # Breakout to the downside
        if not self.is_breaking_out and current_price < range_low - self.breakout_threshold_atr * atr:
            self.is_breaking_out = True
            self.breakout_direction = "short"
            self.breakout_price = current_price
            return None # Wait for the failure to confirm

        # 2. Detect failure (price reverts significantly back into the range)
        if self.is_breaking_out:
            if self.breakout_direction == "long":
                # Failed long breakout: price falls back into range
                if current_price < range_high - self.reversion_threshold_atr * atr:
                    self.is_breaking_out = False # Reset state
                    self.breakout_direction = None
                    self.breakout_price = None
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "failed_long_range_breakout_reversion",
                            "current_price": current_price,
                            "range_high": range_high,
                            "range_low": range_low,
                            "atr": atr,
                            "breakout_threshold_atr": self.breakout_threshold_atr,
                            "reversion_threshold_atr": self.reversion_threshold_atr,
                            "regime": ctx.regime,
                            "bar_index": ctx.bar_index
                        }
                    )
            elif self.breakout_direction == "short":
                # Failed short breakout: price rises back into range
                if current_price > range_low + self.reversion_threshold_atr * atr:
                    self.is_breaking_out = False # Reset state
                    self.breakout_direction = None
                    self.breakout_price = None
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "failed_short_range_breakout_reversion",
                            "current_price": current_price,
                            "range_high": range_high,
                            "range_low": range_low,
                            "atr": atr,
                            "breakout_threshold_atr": self.breakout_threshold_atr,
                            "reversion_threshold_atr": self.reversion_threshold_atr,
                            "regime": ctx.regime,
                            "bar_index": ctx.bar_index
                        }
                    )
            
            # If still breaking out but not failed, do nothing yet
            return None

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any state that should clear after a position closes
        # For this strategy, the cooldown is handled in on_bar
        pass