from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityStructurePullback(Strategy):
    METADATA = {
        "name": "VolatilityStructurePullback",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": [], # No external features used
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 30 # For identifying swing points
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.cooldown_bars

    def _is_higher_high(self, highs, index):
        if index < 1: return False
        return highs[index] > highs[index-1]

    def _is_higher_low(self, lows, index):
        if index < 1: return False
        return lows[index] > lows[index-1]

    def _is_lower_high(self, highs, index):
        if index < 1: return False
        return highs[index] < highs[index-1]

    def _is_lower_low(self, lows, index):
        if index < 1: return False
        return lows[index] < lows[index-1]

    def _is_swing_high(self, highs, index, period):
        if index < period or index >= len(highs) - period:
            return False
        return all(highs[index] > highs[i] for i in range(index - period, index)) and \
               all(highs[index] > highs[i] for i in range(index + 1, index + period + 1))

    def _is_swing_low(self, lows, index, period):
        if index < period or index >= len(lows) - period:
            return False
        return all(lows[index] < lows[i] for i in range(index - period, index)) and \
               all(lows[index] < lows[i] for i in range(index + 1, index + period + 1))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.lookback_period + 1:
            return None

        if ctx.has_position():
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_close = ctx.bar.close

        # Look for the immediate prior swing point
        recent_high_idx = -1
        recent_low_idx = -1

        for i in range(len(highs) - 2, 0, -1): # Iterate backwards from second to last bar
            if self._is_swing_high(highs, i, 2): # Small period for recent swings
                recent_high_idx = i
                break
            if self._is_swing_low(lows, i, 2):
                recent_low_idx = i
                break

        # Bullish setup: Higher Highs, Higher Lows, then a pullback to a prior low or support
        if recent_low_idx != -1 and recent_high_idx != -1 and recent_low_idx < recent_high_idx:
            # We have a low, then a high. Check for subsequent higher low.
            # Look for HH, HL sequence
            # Check if the recent price action indicates an uptrend (HH, HL)
            if self._is_higher_high(highs, recent_high_idx) and self._is_higher_low(lows, recent_low_idx):
                # Now look for a pullback that holds the structure
                # A pullback is when price drops after a high, but stays above the previous low
                if current_close < highs[-2] and current_close > lows[recent_low_idx]:
                    # Check for a clean event boundary: current close must be above previous bar's low
                    # and the previous bar must have been a down bar
                    if ctx.bar.close > ctx.lows(2)[0] and ctx.closes(2)[0] < ctx.opens(2)[0]:
                        # Confirm with bullish candle on current bar
                        if ctx.bar.close > ctx.bar.open:
                            confidence = 0.6 + ctx.rng.uniform(0, 0.2)
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal("long", confidence=confidence, metadata={
                                "reason": "bullish_vol_structure_pullback",
                                "current_close": current_close,
                                "recent_high": highs[recent_high_idx],
                                "recent_low": lows[recent_low_idx],
                                "pullback_magnitude": (highs[-2] - current_close) / highs[-2],
                                "regime": ctx.regime
                            })

        # Bearish setup: Lower Highs, Lower Lows, then a rally to a prior high or resistance
        elif recent_high_idx != -1 and recent_low_idx != -1 and recent_high_idx < recent_low_idx:
            # We have a high, then a low. Check for subsequent lower high.
            # Check if the recent price action indicates a downtrend (LH, LL)
            if self._is_lower_high(highs, recent_high_idx) and self._is_lower_low(lows, recent_low_idx):
                # Now look for a rally that holds the structure
                # A rally is when price rises after a low, but stays below the previous high
                if current_close > lows[-2] and current_close < highs[recent_high_idx]:
                    # Check for a clean event boundary: current close must be below previous bar's high
                    # and the previous bar must have been an up bar
                    if ctx.bar.close < ctx.highs(2)[0] and ctx.closes(2)[0] > ctx.opens(2)[0]:
                        # Confirm with bearish candle on current bar
                        if ctx.bar.close < ctx.bar.open:
                            confidence = 0.6 + ctx.rng.uniform(0, 0.2)
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal("short", confidence=confidence, metadata={
                                "reason": "bearish_vol_structure_rally",
                                "current_close": current_close,
                                "recent_high": highs[recent_high_idx],
                                "recent_low": lows[recent_low_idx],
                                "rally_magnitude": (current_close - lows[-2]) / lows[-2],
                                "regime": ctx.regime
                            })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to enforce cooldown after any close
        self.last_trade_bar = ctx.bar_index
        pass