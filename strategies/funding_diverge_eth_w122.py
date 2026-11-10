from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingPriceDivergence(Strategy):
    METADATA = {
        "name": "Funding Price Divergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # ~3 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.funding_lookback = 12 # hours for funding average
        self.price_change_lookback = 6 # bars for price change
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_signal_bar = -self.cooldown_bars # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        
        # Calculate EMAs
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Price change over lookback period
        if len(closes) < self.price_change_lookback + 1:
            return None
        price_change_pct = (current_price - closes[-self.price_change_lookback - 1]) / closes[-self.price_change_lookback - 1] * 100

        # Funding rate data
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        
        # We need historical funding rates for an average.
        # Since ctx.features.get only gives current, we need to adapt.
        # For simplicity, we'll use the current funding rate and assume it's indicative
        # or that a platform feature provides historical funding rates if available.
        # For now, we'll use current_funding directly for divergence detection.
        # In a real scenario, a custom feature pipeline would provide `funding_rates(n)`.
        
        # Causal Structure: Price moves, then funding rate reacts or diverges.
        # We look for divergence where price moves one way, but funding rate (representing short-term sentiment/positioning)
        # moves in the opposite direction, suggesting a potential squeeze or liquidation event.

        signal_direction = None
        confidence = 0.0
        reason = "no_signal"
        
        # Long signal:
        # Price is rising (fast EMA > slow EMA and price change is positive)
        # BUT funding rate is negative (shorts are paying longs), indicating short-covering potential.
        if fast_ema > slow_ema and current_price > fast_ema and price_change_pct > 0.1: # Price is showing upward momentum
            if current_funding < -0.00005: # Significantly negative funding (e.g., -0.005% per 8h)
                signal_direction = "long"
                confidence = 0.65 + ctx.rng.uniform(-0.05, 0.05) # Add a touch of randomness for confidence
                reason = "price_rising_negative_funding_short_squeeze_potential"

        # Short signal:
        # Price is falling (fast EMA < slow EMA and price change is negative)
        # BUT funding rate is positive (longs are paying shorts), indicating long-liquidation potential.
        elif fast_ema < slow_ema and current_price < fast_ema and price_change_pct < -0.1: # Price is showing downward momentum
            if current_funding > 0.00005: # Significantly positive funding (e.g., +0.005% per 8h)
                signal_direction = "short"
                confidence = 0.65 + ctx.rng.uniform(-0.05, 0.05)
                reason = "price_falling_positive_funding_long_liquidation_potential"

        if signal_direction and not ctx.has_position():
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_price": current_price,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "price_change_pct": price_change_pct,
                    "current_funding_rate": current_funding,
                },
            )
        
        # If we have a position, check for exit conditions based on price action
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if momentum reverses (fast EMA crosses below slow EMA)
                if fast_ema < slow_ema:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "exit_long_momentum_reversal",
                            "current_price": current_price,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                        },
                    )
            elif ctx.position_direction() == "short":
                # Exit short if momentum reverses (fast EMA crosses above slow EMA)
                if fast_ema > slow_ema:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "exit_short_momentum_reversal",
                            "current_price": current_price,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                        },
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log closing details or adapt strategy if needed
        pass