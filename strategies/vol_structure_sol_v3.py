from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityStructureSOL(Strategy):
    METADATA = {
        "name": "VolatilityStructureSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 50,
        "required_features": [], # No external features used for this pure price-action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.last_trade_bar = -self.cooldown_bars - 1 # Allow trading from start
        self.period_short = 5 # For detecting recent structure
        self.period_long = 20 # For overall trend context
        self.min_volatility_multiplier = 1.0 # Minimum price move to consider it a valid swing
        self.max_pullback_pct = 0.02 # Max pullback before considering structure broken
        self.min_swing_length = 3 # Minimum bars for a swing
        self.structure_lookback = 10 # How many bars to look back for HH/LL
        self.recent_high = -1.0
        self.recent_low = float('inf')

    def _is_higher_high(self, highs, current_bar_idx, lookback):
        if current_bar_idx < lookback:
            return False
        return highs[current_bar_idx] > max(highs[current_bar_idx-lookback:current_bar_idx])

    def _is_lower_low(self, lows, current_bar_idx, lookback):
        if current_bar_idx < lookback:
            return False
        return lows[current_bar_idx] < min(lows[current_bar_idx-lookback:current_bar_idx])

    def _is_higher_low(self, lows, current_bar_idx, lookback):
        if current_bar_idx < lookback + 1: # Need at least two points to compare
            return False
        # Find previous significant low
        prev_low = min(lows[current_bar_idx-lookback:current_bar_idx])
        return lows[current_bar_idx] > prev_low

    def _is_lower_high(self, highs, current_bar_idx, lookback):
        if current_bar_idx < lookback + 1:
            return False
        # Find previous significant high
        prev_high = max(highs[current_bar_idx-lookback:current_bar_idx])
        return highs[current_bar_idx] < prev_high

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period_long + 1)
        opens = ctx.opens(self.period_long + 1)
        highs = ctx.highs(self.period_long + 1)
        lows = ctx.lows(self.period_long + 1)
        volumes = ctx.volumes(self.period_long + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Detect recent price action and structure
        recent_closes = ctx.closes(self.structure_lookback + 1)
        recent_highs = ctx.highs(self.structure_lookback + 1)
        recent_lows = ctx.lows(self.structure_lookback + 1)

        if len(recent_closes) < self.structure_lookback + 1:
            return None

        # Long entry: Look for higher highs and higher lows, then a pullback that holds the structure
        is_uptrend_structure = False
        if len(recent_highs) > self.min_swing_length + 1 and len(recent_lows) > self.min_swing_length + 1:
            # Check for a sequence of HH and HL
            recent_max = max(recent_highs[-self.min_swing_length-1:-1])
            recent_min = min(recent_lows[-self.min_swing_length-1:-1])

            # Check if current high is higher than recent max, and current low is higher than recent min
            if ctx.bar.high > recent_max and ctx.bar.low > recent_min:
                # We need to see a clear move up before a pullback
                # Look for a higher high followed by a higher low (pullback)
                # Simple check: current close is above previous X closes, but has pulled back from its recent peak
                if current_close > recent_closes[-2] and current_close > recent_closes[-self.min_swing_length]:
                    # Check for a pullback
                    peak_since_last_low = max(highs[-self.min_swing_length:])
                    pullback_from_peak_pct = (peak_since_last_low - current_close) / peak_since_last_low if peak_since_last_low > 0 else 0
                    if 0.005 < pullback_from_peak_pct < self.max_pullback_pct: # Significant but not too deep pullback
                        # Ensure the pullback still respects higher low structure
                        # Current low should be higher than the lowest low in the last structure_lookback bars, excluding current
                        lowest_low_in_lookback = min(recent_lows[:-1])
                        if current_low > lowest_low_in_lookback:
                            is_uptrend_structure = True

        # Short entry: Look for lower lows and lower highs, then a pullback that holds the structure
        is_downtrend_structure = False
        if len(recent_highs) > self.min_swing_length + 1 and len(recent_lows) > self.min_swing_length + 1:
            # Check for a sequence of LL and LH
            recent_max = max(recent_highs[-self.min_swing_length-1:-1])
            recent_min = min(recent_lows[-self.min_swing_length-1:-1])

            # Check if current low is lower than recent min, and current high is lower than recent max
            if ctx.bar.low < recent_min and ctx.bar.high < recent_max:
                # We need to see a clear move down before a pullback
                # Look for a lower low followed by a lower high (pullback)
                # Simple check: current close is below previous X closes, but has pulled back from its recent trough
                if current_close < recent_closes[-2] and current_close < recent_closes[-self.min_swing_length]:
                    # Check for a pullback (bounce)
                    trough_since_last_high = min(lows[-self.min_swing_length:])
                    pullback_from_trough_pct = (current_close - trough_since_last_high) / trough_since_last_high if trough_since_last_high > 0 else 0
                    if 0.005 < pullback_from_trough_pct < self.max_pullback_pct: # Significant but not too deep bounce
                        # Ensure the pullback still respects lower high structure
                        # Current high should be lower than the highest high in the last structure_lookback bars, excluding current
                        highest_high_in_lookback = max(recent_highs[:-1])
                        if current_high < highest_high_in_lookback:
                            is_downtrend_structure = True

        signal = None
        if not ctx.has_position():
            if is_uptrend_structure:
                signal = ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "uptrend_pullback_continuation",
                        "current_close": current_close,
                        "recent_high": max(recent_highs),
                        "recent_low": min(recent_lows),
                    }
                )
            elif is_downtrend_structure:
                signal = ctx.signal(
                    "short",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "downtrend_pullback_continuation",
                        "current_close": current_close,
                        "recent_high": max(recent_highs),
                        "recent_low": min(recent_lows),
                    }
                )
        
        if signal:
            self.last_trade_bar = ctx.bar_index
        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset state or log if needed
        pass