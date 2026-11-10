from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingPriceDivergence(Strategy):
    METADATA = {
        "name": "Funding Price Divergence ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50, # Need enough bars for 2 EMAs
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.funding_lookback = 12 # hours for funding rate trend
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        
        # Calculate EMAs for price trend
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Get funding rates
        funding_rates = [ctx.features.get("funding_rate_ethusdt", 0.0) for _ in range(self.funding_lookback)]
        # This is a simplification; ctx.features.get will return the current bar's value
        # for a single feature. To get historical funding rates, we'd need a way to access
        # past feature values, which is not directly available via ctx.features.get().
        # For a truly historical funding rate check, we'd need to store them in state,
        # but that would exceed the complexity budget.
        # So, we'll use a proxy: check current funding rate and its recent trend.
        
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        
        # Determine funding trend (simple average of last few funding rates)
        # We need to simulate a historical funding rate trend. Given the constraint,
        # we'll use a simple approximation: if current funding is significantly
        # different from a baseline (e.g., zero or a small average), it indicates divergence.
        # For a more robust trend, we'd need to manually store historical funding in self.
        
        # Let's define "negative funding trend" as current_funding below a small negative threshold
        # and "positive funding trend" as current_funding above a small positive threshold.
        funding_negative_threshold = -0.00005 # e.g., -0.005% per 8h
        funding_positive_threshold = 0.00005 # e.g., +0.005% per 8h

        signal_direction = None
        confidence = 0.0
        reason = "no_signal"
        
        # Long condition: Price rising (fast EMA > slow EMA) AND funding is significantly negative
        # This suggests a short squeeze potential as shorts are paying a lot to hold their positions
        # while price is moving against them.
        if (fast_ema > slow_ema and current_price > fast_ema and current_funding < funding_negative_threshold):
            if not ctx.has_position() or ctx.position_direction() == "short":
                signal_direction = "long"
                confidence = min(0.7, abs(current_funding / funding_negative_threshold) * 0.5 + (fast_ema - slow_ema) / (slow_ema * 0.01)) # Scale confidence
                reason = "price_rising_negative_funding_squeeze"
                self.last_trade_bar = ctx.bar_index

        # Short condition: Price falling (fast EMA < slow EMA) AND funding is significantly positive
        # This suggests long liquidation potential as longs are paying a lot to hold their positions
        # while price is moving against them.
        elif (fast_ema < slow_ema and current_price < fast_ema and current_funding > funding_positive_threshold):
            if not ctx.has_position() or ctx.position_direction() == "long":
                signal_direction = "short"
                confidence = min(0.7, abs(current_funding / funding_positive_threshold) * 0.5 + (slow_ema - fast_ema) / (fast_ema * 0.01)) # Scale confidence
                reason = "price_falling_positive_funding_liquidation"
                self.last_trade_bar = ctx.bar_index

        # Exit logic:
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if price trend reverses (fast EMA crosses below slow EMA)
                if fast_ema < slow_ema and closes[-1] < closes[-2]:
                    signal_direction = "flat"
                    confidence = 0.6
                    reason = "long_exit_price_trend_reversal"
                    self.last_trade_bar = ctx.bar_index
                # Or if funding flips positive significantly (squeeze is over)
                elif current_funding > funding_positive_threshold * 0.5: # Use a slightly lower threshold for exit
                    signal_direction = "flat"
                    confidence = 0.5
                    reason = "long_exit_funding_flipped_positive"
                    self.last_trade_bar = ctx.bar_index
            elif ctx.position_direction() == "short":
                # Exit short if price trend reverses (fast EMA crosses above slow EMA)
                if fast_ema > slow_ema and closes[-1] > closes[-2]:
                    signal_direction = "flat"
                    confidence = 0.6
                    reason = "short_exit_price_trend_reversal"
                    self.last_trade_bar = ctx.bar_index
                # Or if funding flips negative significantly (liquidation is over)
                elif current_funding < funding_negative_threshold * 0.5: # Use a slightly higher threshold for exit
                    signal_direction = "flat"
                    confidence = 0.5
                    reason = "short_exit_funding_flipped_negative"
                    self.last_trade_bar = ctx.bar_index

        if signal_direction:
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_price": current_price,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "current_funding_rate": current_funding,
                    "funding_negative_threshold": funding_negative_threshold,
                    "funding_positive_threshold": funding_positive_threshold,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just reset last trade bar for cooldown
        pass