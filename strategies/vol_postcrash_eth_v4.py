from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityStabilizationStrategy(Strategy):
    METADATA = {
        "name": "Volatility Stabilization ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this pure price-action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.volatility_period = 20
        self.stabilization_period = 5
        self.cooldown_bars = 10 # Cooldown after a trade closes
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Regime filter: Only trade in 'normal' or 'volatile' regimes, avoid 'crisis' or 'meltdown'
        current_regime = ctx.market.get("regime", "NORMAL")
        if current_regime in ["CRISIS", "MELTDOWN"]:
            return None

        # Calculate historical volatility (e.g., 20-bar standard deviation of returns)
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        volatility = self._stddev(returns, self.volatility_period)
        if volatility is None:
            return None

        # Check for a sharp flush (high volatility followed by price drop)
        # We need a recent high volatility spike
        recent_volatility_slice = [
            self._stddev([(closes[j] - closes[j-1]) / closes[j-1] for j in range(k - self.volatility_period, k)])
            for k in range(self.volatility_period + 1, len(closes) + 1)
        ]
        recent_volatility_slice = [v for v in recent_volatility_slice if v is not None]

        if len(recent_volatility_slice) < self.volatility_period:
             return None

        # Identify a recent peak in volatility
        max_vol_idx = -1
        max_vol = 0.0
        for i in range(len(recent_volatility_slice) - self.stabilization_period - 1, len(recent_volatility_slice) - 1):
            if recent_volatility_slice[i] > max_vol:
                max_vol = recent_volatility_slice[i]
                max_vol_idx = i

        # Ensure a significant volatility spike occurred recently
        if max_vol_idx == -1 or max_vol < (self._stddev(returns, len(returns)) * 1.5): # Volatility must be 1.5x average
            return None

        # Check for stabilization after the volatility spike
        # Current volatility should be significantly lower than the peak
        current_volatility = recent_volatility_slice[-1]
        if current_volatility > max_vol * 0.7: # Current vol should be at least 30% lower than peak
            return None

        # Price action check: current price is recovering from a recent low but still below a short-term average
        # Low of the last 'stabilization_period' bars should be significantly higher than the low at the volatility spike
        low_at_spike_idx = ctx.bar_index - (len(recent_volatility_slice) - 1 - max_vol_idx) - 1
        if low_at_spike_idx < 0 or low_at_spike_idx >= len(lows):
            return None

        low_at_spike = lows[low_at_spike_idx]
        current_low = min(lows[-self.stabilization_period:])

        # We want to enter after a flush and stabilization, looking for a bounce.
        # Price should be above the low of the stabilization period, but still relatively cheap.
        if ctx.bar.close > current_low * 1.005 and ctx.bar.close < closes[-self.stabilization_period] * 0.99:
            if not ctx.has_position():
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "vol_stabilization_long_entry",
                        "current_volatility": current_volatility,
                        "peak_volatility": max_vol,
                        "low_at_spike": low_at_spike,
                        "current_low_stabilization": current_low,
                        "close": ctx.bar.close,
                        "regime": current_regime
                    }
                )

        if ctx.has_position():
            # If in a position, just hold. The engine handles SL/TP.
            return None

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Record the bar index when a position closes to implement cooldown
        self.last_trade_bar = ctx.bar_index