from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingDivergenceETH(Strategy):
    METADATA = {
        "name": "Funding Divergence ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Roughly 4 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24  # Lookback for average funding rate (24 hours for 1h bars)
        self.price_period = 20  # Period for EMA/SMA for price confirmation
        self.cooldown_bars = 6  # Cooldown after a trade (6 hours)
        self.last_signal_bar_index = -self.cooldown_bars * 2 # Initialize to allow immediate trading

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.price_period + 1)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar_index + self.cooldown_bars:
            return None

        current_funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        
        # We need enough funding rates to calculate an average
        # The funding rate feature is hourly, so we need self.funding_period bars of data
        # Let's assume ctx.features.get("funding_rate_ethusdt", 0.0) gives us the current hourly rate
        # We need to implicitly get past funding rates if we want an average.
        # Given the API, we can only get the current one. So we'll use the current one vs a threshold.
        # For a truly robust avg funding, we'd need a way to access past feature values, which isn't directly exposed for features.
        # Let's simplify and use the current funding rate relative to a "neutral" threshold for divergence.
        # A more advanced version would use an EMA of funding rates if `ctx.features` provided historical values.

        # Price confirmation: simple moving average for trend direction
        sma_price = self._sma(closes, self.price_period)
        if sma_price is None:
            return None

        current_price = ctx.bar.close
        prev_price = closes[-2] if len(closes) >= 2 else current_price

        # Funding rate thresholds (example values, need optimization)
        # Positive funding suggests longs are paying shorts (bullish sentiment)
        # Negative funding suggests shorts are paying longs (bearish sentiment)
        funding_threshold_positive = 0.0001  # e.g., 0.01% per 8h, so 0.0000125 per hour, adjusted for 1h bar
        funding_threshold_negative = -0.0001

        signal_confidence = 0.7

        # Long Squeeze Hypothesis: Price is rising, but funding is negative (shorts paying longs).
        # This suggests shorts are trapped and might be squeezed.
        if (current_price > prev_price and  # Price rising
            current_price > sma_price and  # Price above SMA (bullish momentum)
            current_funding_rate < funding_threshold_negative and # Significantly negative funding
            not ctx.has_position()):

            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=signal_confidence,
                metadata={
                    "reason": "funding_divergence_long_squeeze",
                    "current_price": current_price,
                    "prev_price": prev_price,
                    "sma_price": sma_price,
                    "current_funding_rate": current_funding_rate,
                }
            )

        # Short Liquidation Hypothesis: Price is falling, but funding is positive (longs paying shorts).
        # This suggests longs are trapped and might be liquidated.
        elif (current_price < prev_price and  # Price falling
              current_price < sma_price and  # Price below SMA (bearish momentum)
              current_funding_rate > funding_threshold_positive and # Significantly positive funding
              not ctx.has_position()):

            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=signal_confidence,
                metadata={
                    "reason": "funding_divergence_short_liquidation",
                    "current_price": current_price,
                    "prev_price": prev_price,
                    "sma_price": sma_price,
                    "current_funding_rate": current_funding_rate,
                }
            )
        
        # If we have a position, hold it. The engine will manage SL/TP.
        # Alternatively, add logic here to exit based on other conditions.
        elif ctx.has_position():
            return ctx.signal("hold", confidence=0.0) # Explicitly hold position

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for debugging/analysis
        pass