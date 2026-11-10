from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedBreakoutReversion(Strategy):
    METADATA = {
        "name": "Failed Breakout Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended avg hold time around 4 hours
        "warmup_bars": 50,
        "required_features": [], # No specific features needed, using OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.range_period = 20 # Period to define the range
        self.breakout_threshold_factor = 0.005 # 0.5% breakout above/below range
        self.reversion_threshold_factor = 0.002 # 0.2% reversion back into range
        self.cooldown_bars = 10 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow trading immediately

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period + 2)
        highs = ctx.highs(self.range_period + 2)
        lows = ctx.lows(self.range_period + 2)

        if len(closes) < self.range_period + 2:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Define the recent range (excluding the current bar and the breakout bar)
        recent_highs = highs[-self.range_period-2:-2]
        recent_lows = lows[-self.range_period-2:-2]

        if not recent_highs or not recent_lows:
            return None

        range_high = max(recent_highs)
        range_low = min(recent_lows)
        range_mid = (range_high + range_low) / 2.0
        range_size = range_high - range_low

        if range_size == 0: # Avoid division by zero
            return None

        # Prices of the bar before current (potential breakout bar)
        prev_close = closes[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]

        # Current bar price
        current_close = closes[-1]

        # Check for failed bullish breakout (price went above range, then came back in)
        # Condition 1: Previous bar broke out above the range
        bullish_breakout_level = range_high * (1 + self.breakout_threshold_factor)
        is_prev_bullish_breakout = prev_high > bullish_breakout_level

        # Condition 2: Current bar has reverted back below a specific level within the range
        # We want to short when price fails to sustain the breakout and comes back down
        bullish_reversion_level = range_high * (1 - self.reversion_threshold_factor)
        is_current_reversion_down = current_close < bullish_reversion_level and current_close < range_high

        # Check for failed bearish breakout (price went below range, then came back in)
        # Condition 1: Previous bar broke out below the range
        bearish_breakout_level = range_low * (1 - self.breakout_threshold_factor)
        is_prev_bearish_breakout = prev_low < bearish_breakout_level

        # Condition 2: Current bar has reverted back above a specific level within the range
        # We want to long when price fails to sustain the breakdown and comes back up
        bearish_reversion_level = range_low * (1 + self.reversion_threshold_factor)
        is_current_reversion_up = current_close > bearish_reversion_level and current_close > range_low

        if ctx.has_position():
            # If in position, we hold unless there's an opposing signal (handled by engine)
            # or if we want to add specific exit logic based on price action within the range.
            # For now, let the engine handle SL/TP or opposing signals.
            return None
        else:
            if is_prev_bullish_breakout and is_current_reversion_down:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "failed_bullish_breakout_reversion",
                        "range_high": range_high,
                        "range_low": range_low,
                        "prev_high": prev_high,
                        "current_close": current_close,
                        "breakout_threshold_factor": self.breakout_threshold_factor,
                        "reversion_threshold_factor": self.reversion_threshold_factor,
                    }
                )
            elif is_prev_bearish_breakout and is_current_reversion_up:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "failed_bearish_breakout_reversion",
                        "range_high": range_high,
                        "range_low": range_low,
                        "prev_low": prev_low,
                        "current_close": current_close,
                        "breakout_threshold_factor": self.breakout_threshold_factor,
                        "reversion_threshold_factor": self.reversion_threshold_factor,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already implement a cooldown in on_bar, so no further action needed here for cooldown.
        # This method can be used for logging or adaptive sizing if needed in the future.
        pass