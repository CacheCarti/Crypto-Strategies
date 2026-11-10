from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilitySqueezeBreakout(Strategy):
    METADATA = {
        "name": "Volatility Squeeze Breakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 60, # Need enough bars for Bollinger Bands and ATR
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.atr_period = 14
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars * 2 # Initialize to allow immediate trade

        # Squeeze detection parameters
        self.squeeze_threshold_bb_atr_ratio = 1.0 # Bollinger Band width / ATR
        self.squeeze_lookback = 5 # Number of bars BB width / ATR must be below threshold

        # Breakout parameters
        self.breakout_multiplier = 0.8 # Multiplier for ATR to define breakout threshold from mean
        self.volume_confirm_factor = 1.2 # Volume must be 1.2x average

        self.last_bb_width = 0.0
        self.in_squeeze = False

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

    def _bollinger(self, closes, period, num_std=2.0):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = self._sma(slice_c, period)
        std = self._stddev(slice_c, period)
        if mean is None or std is None:
            return None, None, None
        return mean, mean + num_std * std, mean - num_std * std

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        # Simple moving average of TR
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.bb_period + self.atr_period) # Need enough for both
        highs = ctx.highs(self.bb_period + self.atr_period)
        lows = ctx.lows(self.bb_period + self.atr_period)
        volumes = ctx.volumes(self.bb_period + self.atr_period)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Don't trade if already in a position
        if ctx.has_position():
            return None

        # Calculate Bollinger Bands and ATR
        bb_mean, bb_upper, bb_lower = self._bollinger(closes, self.bb_period)
        current_atr = self._atr(highs, lows, closes, self.atr_period)

        if bb_mean is None or bb_upper is None or bb_lower is None or current_atr is None:
            return None

        bb_width = bb_upper - bb_lower
        
        # Volatility Squeeze Detection
        # Squeeze condition: BB width / ATR is below a threshold for several bars
        bb_atr_ratios = []
        for i in range(self.squeeze_lookback):
            # Need historical BB and ATR values for this
            # For simplicity, let's use the current bar's ratio and check historical BB width
            # A more robust implementation would re-calculate BB and ATR for each historical bar
            # Given the constraints, we'll simplify this to checking current ratio against a historically low BB width
            
            # Simplified squeeze detection: current BB width is historically low relative to ATR
            # And compare current BB width to previous BB width to detect compression
            prev_bb_mean, prev_bb_upper, prev_bb_lower = self._bollinger(closes[:-1], self.bb_period)
            if prev_bb_mean is None:
                continue
            prev_bb_width = prev_bb_upper - prev_bb_lower
            if bb_width < prev_bb_width and (bb_width / current_atr) < self.squeeze_threshold_bb_atr_ratio:
                self.in_squeeze = True
            else:
                self.in_squeeze = False
            break # Only check current bar for simplicity in squeeze detection

        # Breakout Detection
        # Only trigger if we were in a squeeze and now price breaks out
        if self.in_squeeze:
            # Look for a breakout from the Bollinger Band mean, confirmed by volume
            avg_volume = self._sma(volumes, self.bb_period)
            if avg_volume is None:
                return None

            current_close = ctx.bar.close
            current_volume = ctx.bar.volume
            
            # Long breakout
            if current_close > bb_upper and current_volume > avg_volume * self.volume_confirm_factor:
                self.last_trade_bar = ctx.bar_index
                self.in_squeeze = False # Reset squeeze state
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "volatility_squeeze_long_breakout",
                        "bb_mean": bb_mean,
                        "bb_upper": bb_upper,
                        "current_close": current_close,
                        "current_volume": current_volume,
                        "avg_volume": avg_volume,
                        "atr": current_atr,
                        "bb_width_atr_ratio": bb_width / current_atr
                    }
                )
            
            # Short breakout
            elif current_close < bb_lower and current_volume > avg_volume * self.volume_confirm_factor:
                self.last_trade_bar = ctx.bar_index
                self.in_squeeze = False # Reset squeeze state
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "volatility_squeeze_short_breakout",
                        "bb_mean": bb_mean,
                        "bb_lower": bb_lower,
                        "current_close": current_close,
                        "current_volume": current_volume,
                        "avg_volume": avg_volume,
                        "atr": current_atr,
                        "bb_width_atr_ratio": bb_width / current_atr
                    }
                )
        
        # If no squeeze condition met or no breakout, keep track of BB width for next bar's comparison
        self.last_bb_width = bb_width
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar on position close to enforce cooldown
        self.last_trade_bar = ctx.bar_index