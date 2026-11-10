from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityFadeStrategy(Strategy):
    METADATA = {
        "name": "Volatility Fade Strategy",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
        "required_features": [], # No external features used, only OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.momentum_period = 10
        self.volatility_period = 20
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_signal_bar = -self.cooldown_bars

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.volatility_period + 1)
        highs = ctx.highs(self.volatility_period + 1)
        lows = ctx.lows(self.volatility_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Calculate momentum (simple rate of change)
        if len(closes) < self.momentum_period + 1:
            return None
        momentum_current = (closes[-1] - closes[-1 - self.momentum_period]) / closes[-1 - self.momentum_period]
        momentum_prev = (closes[-2] - closes[-2 - self.momentum_period]) / closes[-2 - self.momentum_period] if len(closes) >= self.momentum_period + 2 else momentum_current

        # Calculate volatility (standard deviation of returns)
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        volatility = self._stddev(returns, self.volatility_period)

        if volatility is None:
            return None

        # Check for existing position
        if ctx.has_position():
            # Exit logic: if volatility drops significantly or momentum reverses
            current_direction = ctx.position_direction()
            
            # Simple profit taking or stop loss based on volatility
            # This is an additional condition, the engine's TP/SL will also trigger
            if current_direction == "long":
                if momentum_current < 0 and momentum_prev > 0: # Momentum turning down
                     return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_momentum_reversal", "momentum_current": momentum_current, "momentum_prev": momentum_prev, "volatility": volatility})
            elif current_direction == "short":
                if momentum_current > 0 and momentum_prev < 0: # Momentum turning up
                     return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_momentum_reversal", "momentum_current": momentum_current, "momentum_prev": momentum_prev, "volatility": volatility})
            
            return None # Hold position if no exit condition met

        # Entry logic: Fade the third successive directional push when momentum weakens and volatility is high
        # We define a "directional push" as a significant move in one direction.
        # We'll look for 3 consecutive bars closing in the same direction, followed by weakening momentum.

        # Look for 3 consecutive up/down closes
        up_closes = 0
        down_closes = 0
        for i in range(1, 4): # Check last 3 bars
            if closes[-i] > closes[-i-1]:
                up_closes += 1
            elif closes[-i] < closes[-i-1]:
                down_closes += 1
            else:
                break # Not a clear directional push

        # Condition 1: High volatility
        # We define "high volatility" as current volatility being above its recent average
        # To avoid overtrading, we use a simple threshold or comparison to a longer-term average.
        # For simplicity, let's use a fixed threshold for now, or compare to a simple moving average of volatility itself.
        # A simple threshold for BTC 1h: e.g. 0.005 (0.5% std dev of returns) might indicate elevated volatility.
        # This needs to be tuned, but for a general strategy, we'll use a relative measure.
        avg_volatility = self._sma([math.sqrt((c - closes[i-1])**2) / closes[i-1] for i, c in enumerate(closes[1:])], self.volatility_period)
        
        # Ensure avg_volatility is not None and volatility is not None
        if avg_volatility is None or volatility is None:
            return None

        is_high_volatility = volatility > (avg_volatility * 1.2) # Current volatility 20% higher than recent average
        
        # Condition 2: Weakening momentum after 3 pushes
        # Long entry (fading a down move): 3 consecutive down closes, but current momentum is less negative than previous
        if down_closes >= 3 and momentum_current > momentum_prev and closes[-1] < closes[-2]: # Still trending down but momentum weakening
            if is_high_volatility:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.7,
                                  metadata={"reason": "fade_3_down_pushes_weak_momentum_high_vol",
                                            "down_closes": down_closes,
                                            "momentum_current": momentum_current,
                                            "momentum_prev": momentum_prev,
                                            "volatility": volatility,
                                            "avg_volatility": avg_volatility,
                                            "close_change": (closes[-1] - closes[-2])})

        # Short entry (fading an up move): 3 consecutive up closes, but current momentum is less positive than previous
        if up_closes >= 3 and momentum_current < momentum_prev and closes[-1] > closes[-2]: # Still trending up but momentum weakening
            if is_high_volatility:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.7,
                                  metadata={"reason": "fade_3_up_pushes_weak_momentum_high_vol",
                                            "up_closes": up_closes,
                                            "momentum_current": momentum_current,
                                            "momentum_prev": momentum_prev,
                                            "volatility": volatility,
                                            "avg_volatility": avg_volatility,
                                            "close_change": (closes[-1] - closes[-2])})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We use a hard cooldown, so no adaptive logic here, just reset last_signal_bar on entry.
        pass