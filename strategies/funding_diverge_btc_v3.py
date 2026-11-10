from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingDivergence(Strategy):
    METADATA = {
        "name": "BTC Funding Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24  # Lookback for funding rate trend
        self.price_sma_period = 20
        self.funding_threshold = -0.00005  # Negative funding rate threshold for long
        self.pos_funding_threshold = 0.00005 # Positive funding rate threshold for short
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.price_sma_period + 1)
        if len(closes) < self.price_sma_period + 1:
            return None

        current_price = ctx.bar.close
        price_sma = self._sma(closes, self.price_sma_period)
        if price_sma is None:
            return None

        funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0)]
        # Get historical funding rates (approximate by using current for past bars if not available or if feature is daily)
        # Note: funding_rate_btcusdt is hourly, so we can use it directly.
        # For simplicity, we'll just use the current funding rate and check its trend.
        
        # To detect funding trend, we need a few past values.
        # Since ctx.features.get() only gives current bar's feature, we need to store history or rely on current value.
        # For this strategy, we'll simplify and check if the current funding rate is below/above a threshold,
        # combined with price action, without needing a complex funding trend calculation.

        current_funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        signal = None
        confidence = 0.0

        # Long condition: Price rising (above SMA) AND negative funding (potential short squeeze)
        if current_price > price_sma and current_funding < self.funding_threshold:
            if not ctx.has_position() or ctx.position_direction() == "short":
                confidence = 0.7 + ctx.rng.uniform(0.0, 0.1) # Add some randomness for confidence
                signal = ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "funding_divergence_long",
                        "price": current_price,
                        "price_sma": price_sma,
                        "funding_rate": current_funding,
                        "funding_threshold": self.funding_threshold,
                    }
                )

        # Short condition: Price falling (below SMA) AND positive funding (potential long liquidation)
        elif current_price < price_sma and current_funding > self.pos_funding_threshold:
            if not ctx.has_position() or ctx.position_direction() == "long":
                confidence = 0.7 + ctx.rng.uniform(0.0, 0.1)
                signal = ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "funding_divergence_short",
                        "price": current_price,
                        "price_sma": price_sma,
                        "funding_rate": current_funding,
                        "funding_threshold": self.pos_funding_threshold,
                    }
                )
        
        if signal:
            self.last_signal_bar = ctx.bar_index
            return signal

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Add adaptive logic here if needed, e.g., adjust thresholds based on performance
        pass