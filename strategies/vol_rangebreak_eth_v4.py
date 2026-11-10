from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRangeBreakETH(Strategy):
    METADATA = {
        "name": "Volatility Range Break ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20  # Period for range definition
        self.breakout_threshold_factor = 0.005  # Percentage above/below range for breakout
        self.confirmation_bars = 2 # Bars to confirm failed breakout
        self.cooldown_bars = 12 # Bars to wait after a trade
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
        closes = ctx.closes(self.lookback_period + self.confirmation_bars + 1)
        highs = ctx.highs(self.lookback_period + self.confirmation_bars + 1)
        lows = ctx.lows(self.lookback_period + self.confirmation_bars + 1)

        if len(closes) < self.lookback_period + self.confirmation_bars + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filter: only trade in 'normal' or 'volatile' regimes, avoid 'crisis'
        if ctx.regime == "crisis":
            return None
        
        # Trend filter: only take long in bull, short in bear, or trade both in neutral
        trend_regime = ctx.market.get("trend_regime", "neutral")
        
        # Define the range using the lookback_period
        range_high = max(highs[-(self.lookback_period + self.confirmation_bars):-self.confirmation_bars])
        range_low = min(lows[-(self.lookback_period + self.confirmation_bars):-self.confirmation_bars])
        
        current_price = ctx.bar.close

        # Check for breakout in the current bar
        is_breakout_up = current_price > range_high * (1 + self.breakout_threshold_factor)
        is_breakout_down = current_price < range_low * (1 - self.breakout_threshold_factor)
        
        # Check for failed breakout
        # A failed breakout is when price breaks out but then reverses back into the range
        # We need at least one bar after the breakout for confirmation
        if len(closes) < self.confirmation_bars + 1:
            return None

        # Check previous bars for breakout and current bar for reversal
        # Long opportunity: price broke below range, then reversed back up strongly
        if not ctx.has_position() and trend_regime in ["bull", "neutral"]:
            # Check if previous bar(s) broke down
            for i in range(1, self.confirmation_bars + 1):
                prev_low = lows[-i-1]
                if prev_low < range_low * (1 - self.breakout_threshold_factor): # A previous bar broke down
                    # And now current price is back firmly inside the range or above it
                    # This implies a failed breakdown
                    if current_price > range_low and current_price > closes[-self.confirmation_bars-1]: # current price is above the low of the breakout bar
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            metadata={
                                "reason": "failed_breakdown_reversal",
                                "current_price": current_price,
                                "range_low": range_low,
                                "breakout_bar_low": prev_low,
                                "trend_regime": trend_regime
                            }
                        )
        
        # Short opportunity: price broke above range, then reversed back down strongly
        if not ctx.has_position() and trend_regime in ["bear", "neutral"]:
            # Check if previous bar(s) broke up
            for i in range(1, self.confirmation_bars + 1):
                prev_high = highs[-i-1]
                if prev_high > range_high * (1 + self.breakout_threshold_factor): # A previous bar broke up
                    # And now current price is back firmly inside the range or below it
                    # This implies a failed breakout
                    if current_price < range_high and current_price < closes[-self.confirmation_bars-1]: # current price is below the high of the breakout bar
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            metadata={
                                "reason": "failed_breakout_reversal",
                                "current_price": current_price,
                                "range_high": range_high,
                                "breakout_bar_high": prev_high,
                                "trend_regime": trend_regime
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Update last trade bar on close to ensure cooldown applies from exit
        self.last_trade_bar = ctx.bar_index
        pass