from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRangeBreak(Strategy):
    METADATA = {
        "name": "VolatilityRangeBreak",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.range_period = 20
        self.breakout_threshold_factor = 0.005 # 0.5% move beyond range
        self.cooldown_bars = 12 # 12 hours cooldown
        self.last_trade_bar = -self.cooldown_bars

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
        closes = ctx.closes(self.range_period + 5) # Need a few extra for lookback
        highs = ctx.highs(self.range_period + 5)
        lows = ctx.lows(self.range_period + 5)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_close = ctx.bar.close

        # Define the range
        range_high = max(highs[-self.range_period-1:-1]) # Exclude current bar
        range_low = min(lows[-self.range_period-1:-1])   # Exclude current bar
        range_mid = (range_high + range_low) / 2.0
        range_width = range_high - range_low

        # Only consider if a clear range exists (not too wide, not too narrow)
        # Use ATR or a percentage of price to define "not too wide/narrow"
        # Here, we'll use a simple percentage of price
        if range_width / current_close < 0.005 or range_width / current_close > 0.03: # 0.5% to 3% range width
            return None

        # Check for a failed breakout
        # A failed breakout means price moved significantly outside the range, then reversed back inside
        
        # Look for a recent bar that broke out
        # A breakout bar is one where its high is significantly above range_high or low significantly below range_low
        
        # Check for a failed UP-breakout (signal for SHORT)
        # Condition 1: Previous bar's high broke significantly above the range_high
        # Condition 2: Current bar's close is back inside the range or below range_mid
        if highs[-2] > range_high * (1 + self.breakout_threshold_factor) and \
           current_close < range_mid and \
           not ctx.has_position():
            
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.7,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_up_breakout_reversal",
                    "range_high": range_high,
                    "range_low": range_low,
                    "breakout_high": highs[-2],
                    "current_close": current_close,
                    "regime": ctx.regime
                }
            )

        # Check for a failed DOWN-breakout (signal for LONG)
        # Condition 1: Previous bar's low broke significantly below the range_low
        # Condition 2: Current bar's close is back inside the range or above range_mid
        if lows[-2] < range_low * (1 - self.breakout_threshold_factor) and \
           current_close > range_mid and \
           not ctx.has_position():
            
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.7,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_down_breakout_reversal",
                    "range_high": range_high,
                    "range_low": range_low,
                    "breakout_low": lows[-2],
                    "current_close": current_close,
                    "regime": ctx.regime
                }
            )
            
        # Exit logic: if price re-tests the breakout level after entry, or if it moves against us
        if ctx.has_position():
            pos = ctx.position
            entry_price = pos["entry_price"]
            
            if pos["direction"] == "short":
                # If short, and price moves back above range_high, exit
                if current_close > range_high:
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "short_exit_range_reclaim", "current_close": current_close, "range_high": range_high})
            elif pos["direction"] == "long":
                # If long, and price moves back below range_low, exit
                if current_close < range_low:
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "long_exit_range_reclaim", "current_close": current_close, "range_low": range_low})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just update last trade bar for cooldown
        self.last_trade_bar = ctx.bar_index
        pass