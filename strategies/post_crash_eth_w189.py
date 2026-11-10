from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostPanicDrift(Strategy):
    METADATA = {
        "name": "Post Panic Drift",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # Longer hold for drift
        "warmup_bars": 60, # Sufficient for 2x slow EMA
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 8
        self.slow_ema_period = 21
        self.drop_period = 12
        self.stabilization_period = 6
        self.min_drop_pct = 0.04 # 4% drop
        self.min_stabilization_flatness = 0.005 # 0.5% max range during stabilization
        self.cooldown_bars = 10
        self.last_signal_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _get_ema_values(self, closes, period):
        if len(closes) < period:
            return None, None
        k = 2.0 / (period + 1)
        ema_values = []
        ema = sum(closes[:period]) / period
        ema_values.append(ema)
        for v in closes[period:]:
            ema = v * k + ema * (1 - k)
            ema_values.append(ema)
        return ema_values[-1], ema_values[-2] # Current and previous EMA

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        if ctx.has_position():
            return None

        # Cooldown check
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        # 1. Detect a sharp drop (panic phase)
        drop_closes = closes[-self.drop_period:]
        if len(drop_closes) < self.drop_period:
            return None

        initial_price = drop_closes[0]
        final_price = drop_closes[-1]
        price_drop_pct = (initial_price - final_price) / initial_price

        # Check if the drop was significant and recent high was significantly above current price
        # and current price is near the low of the drop period
        recent_high = max(highs[-self.drop_period:])
        recent_low = min(lows[-self.drop_period:])
        
        # Ensure the drop period actually saw a significant decline from its peak
        peak_to_trough_drop = (recent_high - recent_low) / recent_high
        
        # Check if current price is significantly below the start of the drop period
        if price_drop_pct < self.min_drop_pct or peak_to_trough_drop < self.min_drop_pct:
            return None

        # 2. Detect stabilization (low volatility after the drop)
        stabilization_closes = closes[-self.stabilization_period:]
        stabilization_highs = highs[-self.stabilization_period:]
        stabilization_lows = lows[-self.stabilization_period:]

        if len(stabilization_closes) < self.stabilization_period:
            return None

        stabilization_range = (max(stabilization_highs) - min(stabilization_lows)) / closes[-1]
        if stabilization_range > self.min_stabilization_flatness:
            return None # Not stable enough

        # 3. EMA confirmation for upward drift
        fast_ema_current, fast_ema_prev = self._get_ema_values(closes, self.fast_ema_period)
        slow_ema_current, slow_ema_prev = self._get_ema_values(closes, self.slow_ema_period)

        if fast_ema_current is None or slow_ema_current is None:
            return None

        # Require fast EMA to be above slow EMA (bullish drift)
        # And fast EMA should be rising
        if not (fast_ema_current > slow_ema_current and fast_ema_current > fast_ema_prev):
            return None

        # 4. Filter by Fear & Greed Index (sentiment)
        # Avoid trading if sentiment is extremely greedy, as further panic might be imminent
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        if fear_greed_index > 75: # Extreme Greed
            return None

        # All conditions met, generate a long signal
        confidence = min(0.5 + (abs(price_drop_pct) * 5), 0.9) # Higher drop -> higher confidence
        confidence = min(confidence, 1.0) # Cap confidence at 1.0

        self.last_signal_bar = ctx.bar_index
        return ctx.signal(
            "long",
            confidence=confidence,
            metadata={
                "reason": "post_panic_drift_long",
                "drop_pct": f"{price_drop_pct:.2%}",
                "stabilization_range_pct": f"{stabilization_range:.2%}",
                "fast_ema": f"{fast_ema_current:.2f}",
                "slow_ema": f"{slow_ema_current:.2f}",
                "fear_greed_index": fear_greed_index,
                "current_price": closes[-1]
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last signal bar on close to allow for quicker re-entry if conditions are met
        # This allows the cooldown to start from the *close* of the previous trade, not just the signal
        # However, the on_bar cooldown logic is still dominant.
        pass