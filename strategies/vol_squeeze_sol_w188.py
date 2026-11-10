from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityCompressionBreakout(Strategy):
    METADATA = {
        "name": "VolatilityCompressionBreakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 100,
        "required_features": [], # No external features needed for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20 # Period for calculating realized volatility
        self.vol_rank_period = 100 # Period for ranking volatility
        self.min_compression_bars = 5 # Minimum bars for volatility to be compressed
        self.breakout_threshold_multiplier = 1.5 # How many ATRs for a breakout
        self.atr_period = 14
        self.cooldown_bars = 10
        self.last_signal_bar = -self.cooldown_bars # Initialize to allow immediate trading

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_rank_period + 1)
        highs = ctx.highs(self.vol_rank_period + 1)
        lows = ctx.lows(self.vol_rank_period + 1)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Calculate recent realized volatility
        recent_closes_for_vol = ctx.closes(self.vol_period)
        if len(recent_closes_for_vol) < self.vol_period:
            return None
        current_vol = self._stddev(recent_closes_for_vol, self.vol_period)
        if current_vol is None or current_vol == 0:
            return None

        # Calculate historical volatilities for ranking
        historical_vols = []
        for i in range(self.vol_period, len(closes) + 1):
            slice_closes = closes[i - self.vol_period:i]
            vol = self._stddev(slice_closes, self.vol_period)
            if vol is not None:
                historical_vols.append(vol)
        
        if len(historical_vols) < self.vol_rank_period:
            return None
        
        # Take the last `vol_rank_period` volatilities
        recent_historical_vols = historical_vols[-self.vol_rank_period:]
        
        # Check for volatility compression (lowest quartile)
        sorted_vols = sorted(recent_historical_vols)
        quartile_threshold = sorted_vols[len(sorted_vols) // 4]

        is_compressed = current_vol <= quartile_threshold

        # Track compression state
        if not hasattr(self, 'compression_count'):
            self.compression_count = 0
            self.was_compressed_last_bar = False

        if is_compressed:
            self.compression_count += 1
            self.was_compressed_last_bar = True
        else:
            if self.was_compressed_last_bar: # If just exited compression
                self.was_compressed_last_bar = False
            else: # Not compressed, not just exited
                self.compression_count = 0 # Reset count if not compressed for a while

        # Only look for breakouts if we've had sufficient compression
        if self.compression_count < self.min_compression_bars:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None or current_atr == 0:
            return None

        # Breakout detection
        # A breakout is defined as price moving significantly beyond the high/low of the compression period
        # For simplicity, we'll use a breakout above/below the previous bar's range by a multiple of ATR
        # This is a simplification of a true "compression range" breakout but fits the bar-by-bar model.
        
        # Consider a breakout if the current close is significantly above/below a recent high/low
        # We need at least 2 bars to check for a breakout from the previous bar's range
        if len(closes) < 2:
            return None

        prev_close = closes[-2]
        current_close = ctx.bar.close
        
        breakout_magnitude = self.breakout_threshold_multiplier * current_atr

        long_condition = current_close > (prev_close + breakout_magnitude)
        short_condition = current_close < (prev_close - breakout_magnitude)

        if ctx.has_position():
            # If we have a position, check for exit conditions
            position = ctx.position
            if position["direction"] == "long" and short_condition:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "compression_breakout_flip_to_short",
                    "current_vol": current_vol,
                    "quartile_threshold": quartile_threshold,
                    "compression_bars": self.compression_count,
                    "atr": current_atr,
                    "breakout_magnitude": breakout_magnitude,
                    "close": current_close,
                    "prev_close": prev_close
                })
            elif position["direction"] == "short" and long_condition:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "compression_breakout_flip_to_long",
                    "current_vol": current_vol,
                    "quartile_threshold": quartile_threshold,
                    "compression_bars": self.compression_count,
                    "atr": current_atr,
                    "breakout_magnitude": breakout_magnitude,
                    "close": current_close,
                    "prev_close": prev_close
                })
            return None # Hold position if no flip signal

        # Enter new position
        if long_condition:
            self.last_signal_bar = ctx.bar_index
            self.compression_count = 0 # Reset after a trade
            return ctx.signal("long", confidence=0.7, metadata={
                "reason": "volatility_compression_breakout_long",
                "current_vol": current_vol,
                "quartile_threshold": quartile_threshold,
                "compression_bars": self.compression_count,
                "atr": current_atr,
                "breakout_magnitude": breakout_magnitude,
                "close": current_close,
                "prev_close": prev_close
            })

        if short_condition:
            self.last_signal_bar = ctx.bar_index
            self.compression_count = 0 # Reset after a trade
            return ctx.signal("short", confidence=0.7, metadata={
                "reason": "volatility_compression_breakout_short",
                "current_vol": current_vol,
                "quartile_threshold": quartile_threshold,
                "compression_bars": self.compression_count,
                "atr": current_atr,
                "breakout_magnitude": breakout_magnitude,
                "close": current_close,
                "prev_close": prev_close
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset compression count to avoid immediate re-entry after a close,
        # forcing the strategy to wait for new compression.
        self.compression_count = 0
        self.was_compressed_last_bar = False