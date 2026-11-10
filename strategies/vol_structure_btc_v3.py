from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityStructureBTC(Strategy):
    METADATA = {
        "name": "Volatility Structure BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6 hours avg hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this structure-based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        self.hh_period = 20 # Period for Higher High detection
        self.ll_period = 20 # Period for Lower Low detection
        self.pullback_threshold_factor = 0.005 # 0.5% pullback from high/low to consider it a pullback
        self.min_pullback_bars = 3 # Minimum bars for a pullback to be considered valid

        self.initial_volatility_period = 10 # For initial range detection
        self.expansion_threshold_factor = 1.5 # Volatility increase for expansion

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate recent volatility (e.g., using ATR)
        atr_period = 14
        if len(highs) < atr_period + 1:
            return None
        
        current_atr = self._atr(highs, lows, closes, atr_period)
        if current_atr is None:
            return None

        # Detect volatility expansion
        # Using a simple rolling average of ATR for comparison
        previous_atrs = [self._atr(highs[:i+1], lows[:i+1], closes[:i+1], atr_period) for i in range(atr_period, len(highs))]
        previous_atrs = [a for a in previous_atrs if a is not None]
        
        if len(previous_atrs) < self.initial_volatility_period:
            return None

        avg_recent_atr = sum(previous_atrs[-self.initial_volatility_period:]) / self.initial_volatility_period

        # Condition 1: Volatility Expansion
        volatility_expansion = current_atr > avg_recent_atr * self.expansion_threshold_factor

        # Condition 2: Higher Highs / Lower Lows structure
        # Detect recent HH or LL sequence
        recent_highs = highs[-self.hh_period:]
        recent_lows = lows[-self.ll_period:]

        is_higher_high_sequence = False
        if len(recent_highs) >= 2:
            # Check if current high is higher than previous high, and previous high was higher than the one before
            if recent_highs[-1] > recent_highs[-2] and recent_highs[-2] > recent_highs[-3]:
                 is_higher_high_sequence = True

        is_lower_low_sequence = False
        if len(recent_lows) >= 2:
            # Check if current low is lower than previous low, and previous low was lower than the one before
            if recent_lows[-1] < recent_lows[-2] and recent_lows[-2] < recent_lows[-3]:
                is_lower_low_sequence = True

        # Condition 3: Pullback detection
        # For long: price pulls back from a recent high in a HH sequence
        # For short: price pulls back from a recent low in a LL sequence

        long_signal = False
        short_signal = False
        
        if is_higher_high_sequence and volatility_expansion:
            # Check for a pullback after a recent high
            peak_high_index = -1
            for i in range(len(highs) - 1, len(highs) - self.min_pullback_bars - 1, -1):
                if highs[i] == max(highs[i:]): # Found a peak
                    peak_high_index = i
                    break
            
            if peak_high_index != -1 and peak_high_index < len(highs) - 1: # Peak was not the current bar
                # Price must have pulled back from this peak
                pullback_amount = (highs[peak_high_index] - closes[-1]) / highs[peak_high_index]
                if pullback_amount > self.pullback_threshold_factor:
                    long_signal = True

        elif is_lower_low_sequence and volatility_expansion:
            # Check for a pullback after a recent low
            trough_low_index = -1
            for i in range(len(lows) - 1, len(lows) - self.min_pullback_bars - 1, -1):
                if lows[i] == min(lows[i:]): # Found a trough
                    trough_low_index = i
                    break
            
            if trough_low_index != -1 and trough_low_index < len(lows) - 1: # Trough was not the current bar
                # Price must have pulled back up from this trough
                pullback_amount = (closes[-1] - lows[trough_low_index]) / lows[trough_low_index]
                if pullback_amount > self.pullback_threshold_factor:
                    short_signal = True

        if ctx.has_position():
            if ctx.position_direction() == "long" and short_signal:
                # Flip long to short
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.7, metadata={
                    "reason": "vol_expansion_ll_sequence_pullback_flip",
                    "current_atr": current_atr,
                    "avg_recent_atr": avg_recent_atr,
                    "pullback_factor": self.pullback_threshold_factor
                })
            elif ctx.position_direction() == "short" and long_signal:
                # Flip short to long
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.7, metadata={
                    "reason": "vol_expansion_hh_sequence_pullback_flip",
                    "current_atr": current_atr,
                    "avg_recent_atr": avg_recent_atr,
                    "pullback_factor": self.pullback_threshold_factor
                })
            return None # Hold current position if no flip signal

        else: # No position
            if long_signal:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.7, metadata={
                    "reason": "vol_expansion_hh_sequence_pullback",
                    "current_atr": current_atr,
                    "avg_recent_atr": avg_recent_atr,
                    "pullback_factor": self.pullback_threshold_factor
                })
            elif short_signal:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.7, metadata={
                    "reason": "vol_expansion_ll_sequence_pullback",
                    "current_atr": current_atr,
                    "avg_recent_atr": avg_recent_atr,
                    "pullback_factor": self.pullback_threshold_factor
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, simply track last trade bar
        pass

    # Helper for ATR calculation
    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        
        # Calculate EMA of TR for ATR
        if len(trs) < period:
            return None

        # Simple SMA for initial ATR for simplicity, or implement EMA
        return sum(trs[-period:]) / period