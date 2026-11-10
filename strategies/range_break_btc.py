from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedBreakoutRangeBTC(Strategy):
    METADATA = {
        "name": "Failed Breakout Range BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold of 4 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.range_period = 40  # Bars to define the range
        self.breakout_threshold_factor = 0.005  # Percentage outside range for a valid breakout
        self.cooldown_bars = 12  # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period + 1)
        highs = ctx.highs(self.range_period + 1)
        lows = ctx.lows(self.range_period + 1)

        if len(closes) < self.range_period + 1:
            return None

        if ctx.has_position():
            # If current bar is a new day, update range but don't exit if already in position
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Define the recent range
        recent_highs = highs[:-1] # Exclude current bar
        recent_lows = lows[:-1]   # Exclude current bar

        if not recent_highs or not recent_lows:
            return None

        range_high = max(recent_highs[-self.range_period:])
        range_low = min(recent_lows[-self.range_period:])

        current_close = ctx.bar.close
        prev_close = closes[-2] # Close of the bar before current

        # Calculate breakout thresholds
        breakout_upper = range_high * (1 + self.breakout_threshold_factor)
        breakout_lower = range_low * (1 - self.breakout_threshold_factor)

        # Fear & Greed Index as a filter
        fear_greed = ctx.features.get("fear_greed_index", 50)
        
        # Check for failed breakout to the upside (bearish signal)
        # Previous bar closed above range_high, current bar closes back inside
        if prev_close > breakout_upper and current_close < range_high:
            if fear_greed > 30: # Avoid shorting in extreme fear
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "failed_breakout_short",
                        "range_high": range_high,
                        "range_low": range_low,
                        "prev_close": prev_close,
                        "current_close": current_close,
                        "fear_greed": fear_greed,
                    },
                )

        # Check for failed breakout to the downside (bullish signal)
        # Previous bar closed below range_low, current bar closes back inside
        if prev_close < breakout_lower and current_close > range_low:
            if fear_greed < 70: # Avoid longing in extreme greed
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "failed_breakout_long",
                        "range_high": range_high,
                        "range_low": range_low,
                        "prev_close": prev_close,
                        "current_close": current_close,
                        "fear_greed": fear_greed,
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not used for adaptive logic in this strategy, but good to have for debugging/logging.
        pass