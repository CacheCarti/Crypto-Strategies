from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityCompressionBreakout(Strategy):
    METADATA = {
        "name": "Volatility Compression Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Target 3-hour hold
        "warmup_bars": 100,  # Need enough bars for ATR and Bollinger Bands
        "required_features": [], # No external features used for this pure price action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.bollinger_period = 20
        self.bollinger_std_dev = 2.0
        self.compression_lookback = 100 # Lookback for volatility quartile
        self.compression_threshold_percentile = 0.25 # Lowest 25% of ATR values
        self.breakout_threshold_multiplier = 0.5 # For breakout above/below Bollinger band
        self.cooldown_bars = 5 # Cooldown period after a trade

        self.last_signal_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _bollinger(self, closes, period, num_std):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.compression_lookback)
        highs = ctx.highs(self.compression_lookback)
        lows = ctx.lows(self.compression_lookback)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_signal_bar + self.cooldown_bars:
            return None

        # Calculate current ATR
        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        # Calculate historical ATRs for compression detection
        historical_atrs = []
        for i in range(self.atr_period, self.compression_lookback):
            atr_val = self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.atr_period)
            if atr_val is not None:
                historical_atrs.append(atr_val)

        if len(historical_atrs) < self.compression_lookback // 2: # Need a reasonable sample
            return None

        historical_atrs.sort()
        compression_threshold_atr = historical_atrs[int(len(historical_atrs) * self.compression_threshold_percentile)]

        # Check for volatility compression
        is_compressed = current_atr <= compression_threshold_atr

        if not is_compressed:
            return None # Only look for breakouts after compression

        # Calculate Bollinger Bands
        bb_mean, bb_upper, bb_lower = self._bollinger(closes, self.bollinger_period, self.bollinger_std_dev)
        if bb_mean is None:
            return None

        last_close = ctx.bar.close
        prev_close = closes[-2]

        # Breakout conditions
        long_condition = (
            prev_close < bb_upper and last_close > bb_upper # Close above upper band
            and last_close > bb_mean + (current_atr * self.breakout_threshold_multiplier) # Confirm with ATR-based threshold
        )

        short_condition = (
            prev_close > bb_lower and last_close < bb_lower # Close below lower band
            and last_close < bb_mean - (current_atr * self.breakout_threshold_multiplier) # Confirm with ATR-based threshold
        )

        if ctx.has_position():
            # If in a position, we only consider exiting or holding
            # This strategy focuses on entry; exits are handled by SL/TP or time.
            # We could add an exit condition here based on volatility expansion or reversal,
            # but for simplicity, we rely on declared SL/TP and hold time.
            return None

        if long_condition:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                metadata={
                    "reason": "volatility_compression_bollinger_breakout_long",
                    "current_atr": current_atr,
                    "compression_threshold_atr": compression_threshold_atr,
                    "bollinger_upper": bb_upper,
                    "bollinger_lower": bb_lower,
                    "bollinger_mean": bb_mean,
                    "last_close": last_close,
                }
            )

        if short_condition:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                metadata={
                    "reason": "volatility_compression_bollinger_breakout_short",
                    "current_atr": current_atr,
                    "compression_threshold_atr": compression_threshold_atr,
                    "bollinger_upper": bb_upper,
                    "bollinger_lower": bb_lower,
                    "bollinger_mean": bb_mean,
                    "last_close": last_close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic implemented for now.
        # Could use this for tracking win/loss rates and adjusting confidence or sizing.
        pass