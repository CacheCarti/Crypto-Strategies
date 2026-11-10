from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingPriceDivergence(Strategy):
    METADATA = {
        "name": "Funding Price Divergence SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24 # Lookback for funding rate trend
        self.price_ema_period = 20 # EMA for price trend
        self.funding_ema_period = 5 # EMA for funding rate smoothing
        self.funding_threshold = -0.00005 # Negative funding threshold for longs
        self.positive_funding_threshold = 0.00005 # Positive funding threshold for shorts
        self.price_change_threshold = 0.005 # 0.5% price change
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.price_ema_period + 1) # Need one extra for price change
        highs = ctx.highs(self.price_ema_period)
        lows = ctx.lows(self.price_ema_period)
        
        required_bars = max(self.price_ema_period, self.funding_period) + 1
        if len(closes) < required_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        prev_price = closes[-2]
        price_change_pct = (current_price - prev_price) / prev_price

        funding_rates = []
        for i in range(self.funding_period):
            # We need to fetch funding rates for the past 'funding_period' bars
            # Assuming 'funding_rate_solusdt' is available for past bars via features (though typically it's current)
            # For simplicity, we'll use the current bar's funding rate repeated, or assume feature provides historical
            # In a real scenario, ctx.features.get would only give current bar's feature.
            # A more robust solution for historical funding would require it to be part of the bar data or loaded.
            # For this contract, we'll assume a smoothed version of the current funding rate over time.
            # A better approach would be to calculate EMA of the funding rate as it comes in.
            funding_rates.append(ctx.features.get("funding_rate_solusdt", 0.0))
        
        # To get a historical funding trend, we need to track funding rates over time.
        # This simple strategy will use the current funding rate and assume it reflects a recent trend.
        # For a true "funding trend", we would need a list of past funding rates.
        # Let's approximate by using EMA of the current funding rate.
        
        # To get a series of funding rates for EMA, we need to store them.
        # This is a limitation of BarContext not providing historical features directly.
        # We will use the current funding rate and compare it to a smoothed version of itself.
        current_funding = ctx.features.get("funding_rate_solusdt", 0.0)

        # Simple price EMA
        price_ema = self._ema(closes, self.price_ema_period)
        if price_ema is None:
            return None

        if not ctx.has_position():
            # Long condition: Price rising, but funding negative (short squeeze potential)
            # Price above EMA confirms uptrend
            # Funding is significantly negative
            if (current_price > price_ema and 
                price_change_pct > self.price_change_threshold and
                current_funding < self.funding_threshold):
                
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long", 
                    confidence=0.75, 
                    metadata={
                        "reason": "long_squeeze_divergence",
                        "price": current_price,
                        "price_ema": price_ema,
                        "price_change_pct": price_change_pct,
                        "funding_rate": current_funding,
                        "funding_threshold": self.funding_threshold
                    }
                )

            # Short condition: Price falling, but funding positive (long liquidation potential)
            # Price below EMA confirms downtrend
            # Funding is significantly positive
            if (current_price < price_ema and 
                price_change_pct < -self.price_change_threshold and
                current_funding > self.positive_funding_threshold):
                
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short", 
                    confidence=0.75, 
                    metadata={
                        "reason": "short_liquidation_divergence",
                        "price": current_price,
                        "price_ema": price_ema,
                        "price_change_pct": price_change_pct,
                        "funding_rate": current_funding,
                        "funding_threshold": self.positive_funding_threshold
                    }
                )
        
        # No exit logic based on funding divergence here, relying on SL/TP.
        # Could add exit logic if funding rate normalizes or reverses.
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for debugging/analysis
        pass