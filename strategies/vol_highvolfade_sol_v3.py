from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityFadeZScore(Strategy):
    METADATA = {
        "name": "VolatilityFadeZScore",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # 4 hours
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 20
        self.atr_period = 14
        self.zscore_threshold = 2.0
        self.volatility_threshold_multiplier = 1.5 # How many ATRs above average to consider "high vol"
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store last z-score direction for transition detection
        self.last_zscore_direction = 0 # 0: neutral, 1: positive, -1: negative

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

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if mean is None or std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.zscore_period + 1) # Need +1 for ATR
        highs = ctx.highs(self.atr_period + 1)
        lows = ctx.lows(self.atr_period + 1)
        volumes = ctx.volumes(self.zscore_period)

        if len(closes) < self.zscore_period + 1 or len(highs) < self.atr_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_zscore = self._zscore(closes, self.zscore_period)
        if current_zscore is None:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        # Calculate average ATR over a longer period to determine relative volatility
        long_atr_period = 48 # ~2 days
        long_highs = ctx.highs(long_atr_period + 1)
        long_lows = ctx.lows(long_atr_period + 1)
        long_closes = ctx.closes(long_atr_period + 1)

        if len(long_closes) < long_atr_period + 1:
            return None
        
        avg_atr_long = self._atr(long_highs, long_lows, long_closes, long_atr_period)
        if avg_atr_long is None or avg_atr_long == 0:
            return None

        # Volatility filter: only trade in top quartile volatility
        # We define top quartile as current ATR significantly higher than its long-term average
        is_high_volatility = (current_atr > avg_atr_long * self.volatility_threshold_multiplier)

        # Regime filter: do not fade calm trends
        # We define "calm trend" as normal regime and z-score not extremely extended
        is_calm_trend = ctx.market.get("regime", "NORMAL") == "NORMAL" and abs(current_zscore) < self.zscore_threshold * 0.5

        # Transition detection for z-score: ensure we are fading an *event* not a continuous drift
        current_zscore_direction = 0
        if current_zscore > self.zscore_threshold:
            current_zscore_direction = 1
        elif current_zscore < -self.zscore_threshold:
            current_zscore_direction = -1

        # Check for clean event boundary: z-score just crossed the threshold
        is_bull_event_boundary = (current_zscore_direction == 1 and self.last_zscore_direction != 1)
        is_bear_event_boundary = (current_zscore_direction == -1 and self.last_zscore_direction != -1)

        self.last_zscore_direction = current_zscore_direction # Update for next bar

        signal = None
        confidence = 0.65 # Base confidence

        if ctx.has_position():
            # If we have a position and conditions reverse, or if z-score returns to neutral, consider exiting
            if (ctx.position_direction() == "long" and current_zscore > 0 and current_zscore < 0.5) or \
               (ctx.position_direction() == "short" and current_zscore < 0 and current_zscore > -0.5):
                signal = ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "zscore_reversion_to_neutral",
                    "current_zscore": current_zscore,
                    "avg_atr_long": avg_atr_long,
                    "current_atr": current_atr
                })
        else:
            if is_high_volatility and not is_calm_trend:
                # Short opportunity: z-score significantly high, suggesting overextension
                if current_zscore > self.zscore_threshold and is_bull_event_boundary:
                    signal = ctx.signal("short", confidence=confidence, metadata={
                        "reason": "zscore_overextension_fade_high_vol",
                        "zscore": current_zscore,
                        "atr_ratio": current_atr / avg_atr_long,
                        "regime": ctx.market.get("regime", "NORMAL"),
                        "volatility_threshold_multiplier": self.volatility_threshold_multiplier
                    })
                # Long opportunity: z-score significantly low, suggesting overextension
                elif current_zscore < -self.zscore_threshold and is_bear_event_boundary:
                    signal = ctx.signal("long", confidence=confidence, metadata={
                        "reason": "zscore_underextension_fade_high_vol",
                        "zscore": current_zscore,
                        "atr_ratio": current_atr / avg_atr_long,
                        "regime": ctx.market.get("regime", "NORMAL"),
                        "volatility_threshold_multiplier": self.volatility_threshold_multiplier
                    })
        
        if signal:
            self.last_trade_bar = ctx.bar_index
        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: adaptive logic based on position outcome
        # For this strategy, simple cooldown is sufficient.
        pass