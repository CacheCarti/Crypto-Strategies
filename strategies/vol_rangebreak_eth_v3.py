from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRangeBreakETH(Strategy):
    METADATA = {
        "name": "VolatilityRangeBreakETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": [], # No external features used for this pure OHLCV strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.slow_period = 30
        self.std_dev_period = 20
        self.breakout_multiplier = 1.5 # How many std devs for a breakout
        self.cooldown_bars = 10 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

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
        closes = ctx.closes(self.slow_period + 1)
        highs = ctx.highs(self.std_dev_period)
        lows = ctx.lows(self.std_dev_period)

        if len(closes) < self.slow_period + 1 or len(highs) < self.std_dev_period or len(lows) < self.std_dev_period:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        prev_close = closes[-2]

        # Calculate short-term and long-term moving averages
        fast_sma = self._sma(closes[:-1], self.fast_period) # Use closes up to previous bar for SMA
        slow_sma = self._sma(closes[:-1], self.slow_period) # Use closes up to previous bar for SMA

        if fast_sma is None or slow_sma is None:
            return None

        # Volatility-based range calculation
        current_stddev = self._stddev(closes[-self.std_dev_period:], self.std_dev_period)
        if current_stddev is None:
            return None

        # Define range based on recent volatility around a moving average
        # We use slow_sma as the center of our range
        upper_band = slow_sma + self.breakout_multiplier * current_stddev
        lower_band = slow_sma - self.breakout_multiplier * current_stddev

        # Entry logic: Failed range breakout
        # Looking for price to move outside the band and then quickly revert back inside,
        # especially when fast_sma is crossing slow_sma in the direction of the reversion.

        if not ctx.has_position():
            # Failed Bearish Breakout (Long Signal)
            # Price breaks below lower band, then closes back above the lower band
            # and fast SMA crosses above slow SMA.
            if prev_close < lower_band and current_close > lower_band and fast_sma < slow_sma and current_close > fast_sma:
                # Ensure it's a recent cross or about to cross bullishly
                if closes[-1] > closes[-2]: # Current bar is bullish
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "failed_bearish_range_breakout",
                            "current_close": current_close,
                            "lower_band": lower_band,
                            "fast_sma": fast_sma,
                            "slow_sma": slow_sma,
                            "std_dev": current_stddev,
                            "regime": ctx.regime,
                        }
                    )

            # Failed Bullish Breakout (Short Signal)
            # Price breaks above upper band, then closes back below the upper band
            # and fast SMA crosses below slow SMA.
            if prev_close > upper_band and current_close < upper_band and fast_sma > slow_sma and current_close < fast_sma:
                # Ensure it's a recent cross or about to cross bearishly
                if closes[-1] < closes[-2]: # Current bar is bearish
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "failed_bullish_range_breakout",
                            "current_close": current_close,
                            "upper_band": upper_band,
                            "fast_sma": fast_sma,
                            "slow_sma": slow_sma,
                            "std_dev": current_stddev,
                            "regime": ctx.regime,
                        }
                    )
        elif ctx.has_position():
            # Simple exit logic: if price crosses back over the fast SMA against the position, consider exiting.
            # Or if it breaks the opposite band.
            if ctx.position_direction() == "long":
                if current_close < fast_sma or current_close < lower_band: # If price drops below fast SMA or re-enters bearish range
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "long_exit_reversion_or_rebreak",
                            "current_close": current_close,
                            "fast_sma": fast_sma,
                            "lower_band": lower_band,
                        }
                    )
            elif ctx.position_direction() == "short":
                if current_close > fast_sma or current_close > upper_band: # If price rises above fast SMA or re-enters bullish range
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "short_exit_reversion_or_rebreak",
                            "current_close": current_close,
                            "fast_sma": fast_sma,
                            "upper_band": upper_band,
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, to ensure we wait self.cooldown_bars bars
        self.last_trade_bar = ctx.bar_index
        # You could also use this for adaptive sizing or logging
        # For example: print(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps in {position['bars_held']} bars.")