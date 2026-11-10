from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingCarryBTC(Strategy):
    METADATA = {
        "name": "FundingCarryBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for longer holds
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24 # Use 24-bar EMA for funding
        self.price_ema_period = 10 # Faster EMA for price confirmation
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_signal_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.price_ema_period + 1)
        if len(closes) < self.price_ema_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Funding rate EMA for trend detection
        funding_rates_history = [ctx.features.get(f"funding_rate_btcusdt", 0.0) for _ in range(self.funding_period + 1)]
        if len(funding_rates_history) < self.funding_period + 1:
            return None
        
        # We need to collect funding rates over time, not just the current one
        # Let's use a simplified approach by checking current vs. a threshold
        # Or, we can simulate an EMA if we don't have enough history directly
        # For simplicity, let's just use the current funding rate and a threshold
        # and assume it has some persistence.

        # Price EMA for confirmation
        price_ema = self._ema(closes, self.price_ema_period)
        if price_ema is None:
            return None

        current_price = ctx.bar.close

        # Long conditions: Favorable funding (negative) AND price above short-term EMA
        long_funding_threshold = -0.00015 # e.g., -0.015% per 8h, or -0.00015 per 1h bar (adjusted for 1h)
        if current_funding_rate < long_funding_threshold and current_price > price_ema:
            if not ctx.has_position() or ctx.position_direction() == "short":
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.7, metadata={
                    "reason": "negative_funding_carry_price_above_ema",
                    "funding_rate": current_funding_rate,
                    "price_ema": price_ema,
                    "current_price": current_price
                })

        # Short conditions: Unfavorable funding (positive) AND price below short-term EMA
        short_funding_threshold = 0.00015 # e.g., 0.015% per 8h, or 0.00015 per 1h bar (adjusted for 1h)
        if current_funding_rate > short_funding_threshold and current_price < price_ema:
            if not ctx.has_position() or ctx.position_direction() == "long":
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.7, metadata={
                    "reason": "positive_funding_carry_price_below_ema",
                    "funding_rate": current_funding_rate,
                    "price_ema": price_ema,
                    "current_price": current_price
                })

        # Exit condition: Funding normalizes or reverses
        if ctx.has_position():
            if ctx.position_direction() == "long" and current_funding_rate > 0: # Funding turns positive
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "long_exit_funding_normalized",
                    "funding_rate": current_funding_rate,
                    "entry_price": ctx.position["entry_price"],
                    "current_price": current_price
                })
            elif ctx.position_direction() == "short" and current_funding_rate < 0: # Funding turns negative
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "short_exit_funding_normalized",
                    "funding_rate": current_funding_rate,
                    "entry_price": ctx.position["entry_price"],
                    "current_price": current_price
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details for debugging or adaptive logic
        pass