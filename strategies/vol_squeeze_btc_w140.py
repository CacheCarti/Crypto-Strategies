from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityCompressionBreakout(Strategy):
    METADATA = {
        "name": "Volatility Compression Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 100, # Longest period for ATR and Bollinger
        "required_features": [] # No external features used for this pure price action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.bollinger_period = 40
        self.bollinger_num_std = 1.5 # Tighter bands for compression detection
        self.compression_threshold_std_dev = 0.5 # Std dev of Bollinger width for compression
        self.breakout_confirmation_bars = 2 # Bars needed to confirm breakout
        self.cooldown_bars = 24 # Cooldown after a trade (1 day)
        self.last_trade_bar = -self.cooldown_bars

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period: # Ensure enough TRs for the average
            return None
        return sum(trs[-period:]) / period

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

    def _bollinger(self, closes, period, num_std):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = self._sma(closes, period)
        std = self._stddev(closes, period)
        if mean is None or std is None:
            return None, None, None
        return mean, mean + num_std * std, mean - num_std * std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.bollinger_period + self.compression_threshold_std_dev * 20) # Need enough for Bollinger and compression history
        highs = ctx.highs(self.bollinger_period + self.compression_threshold_std_dev * 20)
        lows = ctx.lows(self.bollinger_period + self.compression_threshold_std_dev * 20)
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate Bollinger Bands over a historical window
        bb_means = []
        bb_upper_bands = []
        bb_lower_bands = []
        bb_widths = []

        for i in range(self.bollinger_period - 1, len(closes)):
            sub_closes = closes[:i+1]
            mean, upper, lower = self._bollinger(sub_closes, self.bollinger_period, self.bollinger_num_std)
            if mean is not None:
                bb_means.append(mean)
                bb_upper_bands.append(upper)
                bb_lower_bands.append(lower)
                bb_widths.append((upper - lower) / mean) # Normalized width

        if len(bb_widths) < self.bollinger_period: # Need enough widths to calculate std dev of widths
            return None

        current_mean, current_upper, current_lower = bb_means[-1], bb_upper_bands[-1], bb_lower_bands[-1]
        current_width = bb_widths[-1]
        
        # Detect Volatility Compression: Bollinger Band width is significantly below its recent average
        # We look at the standard deviation of the normalized Bollinger width
        recent_widths = bb_widths[-self.bollinger_period:]
        if len(recent_widths) < self.bollinger_period:
            return None

        avg_width = sum(recent_widths) / self.bollinger_period
        std_dev_width = self._stddev(recent_widths, self.bollinger_period)

        is_compressed = False
        if std_dev_width is not None and std_dev_width > 0:
            # Check if current width is significantly below the average width
            if current_width < avg_width - self.compression_threshold_std_dev * std_dev_width:
                is_compressed = True
        
        # Volatility Breakout Logic
        if not ctx.has_position() and is_compressed:
            # Look for breakout confirmation after compression
            # A valid breakout requires the price to close above the upper band (long) or below the lower band (short)
            # AND maintain that for `breakout_confirmation_bars`
            
            # Long breakout check
            long_breakout_confirmed = True
            for i in range(1, self.breakout_confirmation_bars + 1):
                if i > len(bb_upper_bands) or closes[-i] <= bb_upper_bands[-i]:
                    long_breakout_confirmed = False
                    break
            
            if long_breakout_confirmed:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "volatility_compression_long_breakout",
                        "current_close": ctx.bar.close,
                        "bollinger_upper": current_upper,
                        "bollinger_lower": current_lower,
                        "bollinger_width_ratio": current_width,
                        "avg_bollinger_width_ratio": avg_width,
                        "std_dev_bollinger_width_ratio": std_dev_width
                    }
                )

            # Short breakout check
            short_breakout_confirmed = True
            for i in range(1, self.breakout_confirmation_bars + 1):
                if i > len(bb_lower_bands) or closes[-i] >= bb_lower_bands[-i]:
                    short_breakout_confirmed = False
                    break

            if short_breakout_confirmed:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "volatility_compression_short_breakout",
                        "current_close": ctx.bar.close,
                        "bollinger_upper": current_upper,
                        "bollinger_lower": current_lower,
                        "bollinger_width_ratio": current_width,
                        "avg_bollinger_width_ratio": avg_width,
                        "std_dev_bollinger_width_ratio": std_dev_width
                    }
                )

        # Exit logic: if price re-enters the bands or opposite signal
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if price closes below the mean or lower band
                if ctx.bar.close < current_mean:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_exit_reentered_bands",
                            "current_close": ctx.bar.close,
                            "bollinger_mean": current_mean,
                            "bollinger_lower": current_lower
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit short if price closes above the mean or upper band
                if ctx.bar.close > current_mean:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_exit_reentered_bands",
                            "current_close": ctx.bar.close,
                            "bollinger_mean": current_mean,
                            "bollinger_upper": current_upper
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally, you could use this to adjust `cooldown_bars` based on performance
        # For simplicity, we'll just keep the fixed cooldown.
        pass