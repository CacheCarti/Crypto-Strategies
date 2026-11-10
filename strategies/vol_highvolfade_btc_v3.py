from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityFadeBTC(Strategy):
    METADATA = {
        "name": "Volatility Fade BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours intended hold
        "warmup_bars": 60, # Need enough for ATR and Z-score
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 20
        self.atr_period = 20
        self.atr_multiplier = 1.0 # For high volatility threshold
        self.zscore_threshold = 2.0 # Fade moves beyond this Z-score
        self.cooldown_bars = 5 # Bars to wait after a trade closes
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow trade on first bar

        # Store previous Z-score direction to detect transitions
        self.prev_zscore_direction = 0 # 1 for positive, -1 for negative, 0 for neutral

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.zscore_period + 1)
        highs = ctx.highs(self.atr_period + 1)
        lows = ctx.lows(self.atr_period + 1)
        volumes = ctx.volumes(self.zscore_period + 1) # Using same period for simplicity

        if len(closes) < self.METADATA["warmup_bars"] or len(highs) < self.METADATA["warmup_bars"] or len(lows) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_zscore = self._zscore(closes, self.zscore_period)
        current_atr = self._atr(highs, lows, closes, self.atr_period)

        if current_zscore is None or current_atr is None:
            return None

        # Determine if volatility is high (top quartile based on ATR, simplified)
        # For simplicity, we'll compare current ATR to a moving average of ATR
        atr_values = [self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.atr_period) for i in range(self.atr_period, len(closes))]
        atr_values = [v for v in atr_values if v is not None]

        if len(atr_values) < self.atr_period: # Need enough ATR values to compare
            return None
        
        avg_atr = sum(atr_values[-self.atr_period:]) / self.atr_period
        
        # Define high volatility as current ATR significantly above its recent average
        is_high_volatility = current_atr > (avg_atr * self.atr_multiplier)

        # Determine current z-score direction
        current_zscore_direction = 0
        if current_zscore > 0.5: # Use a small buffer to avoid flipping on noise
            current_zscore_direction = 1
        elif current_zscore < -0.5:
            current_zscore_direction = -1

        # Check if we are in a position
        if ctx.has_position():
            # If z-score direction flips while in a trade, consider exiting
            if ctx.position_direction() == "long" and current_zscore < 0:
                 return ctx.signal("flat", confidence=0.7, metadata={"reason": "zscore_turned_negative", "current_zscore": current_zscore, "atr": current_atr})
            if ctx.position_direction() == "short" and current_zscore > 0:
                 return ctx.signal("flat", confidence=0.7, metadata={"reason": "zscore_turned_positive", "current_zscore": current_zscore, "atr": current_atr})
            return None # Hold position if no exit condition met

        # Entry logic: fade extended z-score moves only in high volatility
        # And only if the z-score just crossed the threshold (transition detection)
        signal = None
        if is_high_volatility:
            if current_zscore <= -self.zscore_threshold and self.prev_zscore_direction != -1:
                signal = ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "zscore_oversold_fade_high_volatility",
                        "zscore": current_zscore,
                        "atr": current_atr,
                        "avg_atr": avg_atr,
                        "volatility_status": "high"
                    }
                )
            elif current_zscore >= self.zscore_threshold and self.prev_zscore_direction != 1:
                signal = ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "zscore_overbought_fade_high_volatility",
                        "zscore": current_zscore,
                        "atr": current_atr,
                        "avg_atr": avg_atr,
                        "volatility_status": "high"
                    }
                )
        
        # Update previous z-score direction for transition detection
        self.prev_zscore_direction = current_zscore_direction

        if signal:
            self.last_trade_bar = ctx.bar_index
        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We can log the close reason or adapt behavior if needed.
        # For this strategy, we only use last_trade_bar for cooldown.
        pass