from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional
import math

class FundingDivergence(Strategy):
    METADATA = {
        "name": "Funding Divergence SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.price_period = 20 # For price trend
        self.funding_period = 20 # For funding trend
        self.cooldown_bars = 12 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.price_period + 1)
        funding_rates = [ctx.features.get("funding_rate_solusdt", 0.0) for _ in range(self.funding_period + 1)] # Get funding for past bars

        if len(closes) < self.price_period + 1 or len(funding_rates) < self.funding_period + 1:
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        prev_price = closes[-2]
        current_funding = ctx.features.get("funding_rate_solusdt", 0.0)
        
        # Calculate price trend over the period
        price_sma_current = self._sma(closes, self.price_period)
        price_sma_prev = self._sma(closes[:-1], self.price_period) # SMA ending one bar ago

        if price_sma_current is None or price_sma_prev is None:
            return None

        price_rising = price_sma_current > price_sma_prev
        price_falling = price_sma_current < price_sma_prev

        # Funding trend check (e.g., average funding over period)
        avg_funding_recent = self._sma(funding_rates[-self.funding_period:], self.funding_period)
        
        if avg_funding_recent is None:
            return None

        signal_confidence = 0.65
        
        # Long condition: Price rising, but funding rate remains negative (or very low positive)
        # Suggests shorts are trapped or reluctant to cover despite price rise
        if not ctx.has_position():
            if price_rising and current_price > prev_price and avg_funding_recent < 0.00005: # Funding near or below zero
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=signal_confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "funding_divergence_long",
                        "price_change": (current_price - prev_price) / prev_price * 100,
                        "current_funding": current_funding,
                        "avg_funding_recent": avg_funding_recent,
                        "price_sma_trend": "rising"
                    }
                )

            # Short condition: Price falling, but funding rate remains positive (or very low negative)
            # Suggests longs are trapped or reluctant to sell despite price fall
            if price_falling and current_price < prev_price and avg_funding_recent > -0.00005: # Funding near or above zero
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=signal_confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "funding_divergence_short",
                        "price_change": (current_price - prev_price) / prev_price * 100,
                        "current_funding": current_funding,
                        "avg_funding_recent": avg_funding_recent,
                        "price_sma_trend": "falling"
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: dict) -> None:
        # Optionally adjust state or log
        pass