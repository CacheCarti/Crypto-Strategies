from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedBreakoutReversion(Strategy):
    METADATA = {
        "name": "Failed Range Breakout Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this pure price action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.range_period = 20  # Period to define the range
        self.breakout_threshold_mult = 0.005  # 0.5% above/below range for breakout confirmation
        self.reversion_threshold_mult = 0.002  # 0.2% back into range for reversion signal
        self.cooldown_bars = 12 # Cooldown for 12 hours (12 bars) after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

    def _get_range(self, closes, period):
        if len(closes) < period:
            return None, None
        recent_closes = closes[-period:]
        return min(recent_closes), max(recent_closes)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period + 5) # Need a few extra bars for the breakout
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = ctx.bar.close

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate the recent range (excluding the most recent bars that might be the breakout attempt)
        # We look at the range_period *before* the potential breakout bar
        range_start_index = -(self.range_period + 2) # Look two bars back from current to define the range
        range_end_index = -2
        if len(closes) < abs(range_start_index):
            return None

        base_closes = closes[range_start_index:range_end_index]
        if not base_closes:
            return None

        range_low, range_high = min(base_closes), max(base_closes)

        # Define breakout levels
        long_breakout_level = range_high * (1 + self.breakout_threshold_mult)
        short_breakout_level = range_low * (1 - self.breakout_threshold_mult)

        # Check for a failed long breakout (price went above, then came back in)
        # We need at least two bars after the range for this pattern
        if len(closes) >= range_end_index + 2:
            breakout_bar_close = closes[-2] # The bar that potentially broke out
            reversion_bar_close = closes[-1] # The bar that potentially reverted

            # Failed Long Breakout:
            # 1. Price broke above the range_high + threshold
            # 2. Then, price closed back below range_high - reversion_threshold_mult
            if breakout_bar_close > long_breakout_level and \
               reversion_bar_close < range_high * (1 - self.reversion_threshold_mult):
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("short", confidence=0.75,
                                      stop_loss_bps=self.METADATA["declared_sl_bps"],
                                      take_profit_bps=self.METADATA["declared_tp_bps"],
                                      horizon_seconds=self.METADATA["declared_hold_seconds"],
                                      metadata={
                                          "reason": "failed_long_breakout_reversion",
                                          "current_close": current_close,
                                          "range_high": range_high,
                                          "range_low": range_low,
                                          "breakout_bar_close": breakout_bar_close,
                                          "reversion_bar_close": reversion_bar_close,
                                          "breakout_level": long_breakout_level,
                                          "reversion_level": range_high * (1 - self.reversion_threshold_mult),
                                      })

            # Failed Short Breakout:
            # 1. Price broke below the range_low - threshold
            # 2. Then, price closed back above range_low + reversion_threshold_mult
            elif breakout_bar_close < short_breakout_level and \
                 reversion_bar_close > range_low * (1 + self.reversion_threshold_mult):
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("long", confidence=0.75,
                                      stop_loss_bps=self.METADATA["declared_sl_bps"],
                                      take_profit_bps=self.METADATA["declared_tp_bps"],
                                      horizon_seconds=self.METADATA["declared_hold_seconds"],
                                      metadata={
                                          "reason": "failed_short_breakout_reversion",
                                          "current_close": current_close,
                                          "range_high": range_high,
                                          "range_low": range_low,
                                          "breakout_bar_close": breakout_bar_close,
                                          "reversion_bar_close": reversion_bar_close,
                                          "breakout_level": short_breakout_level,
                                          "reversion_level": range_low * (1 + self.reversion_threshold_mult),
                                      })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic needed for this strategy, but could be added here.
        # self.last_trade_bar = ctx.bar_index # This is already handled by on_bar's successful signal
        pass