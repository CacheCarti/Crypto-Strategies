from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingFlipBTC(Strategy):
    METADATA = {
        "name": "Funding Flip BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 2  # Lookback for funding change
        self.price_confirm_period = 3 # Lookback for price confirmation
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.price_confirm_period + 1)
        funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0) for _ in range(self.funding_period + 1)] # Get current and previous funding rates

        if len(closes) < self.price_confirm_period + 1 or len(funding_rates) < self.funding_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_funding = funding_rates[-1]
        prev_funding = funding_rates[-2] if len(funding_rates) > 1 else 0.0

        # Funding Flip Logic
        # Long signal: Funding flips from negative to positive
        if prev_funding <= 0 and current_funding > 0.00001: # Small threshold to avoid noise around 0
            # Price confirmation: current close must be higher than the close 'price_confirm_period' bars ago
            if closes[-1] > closes[0]:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "funding_flip_positive_price_confirm",
                        "current_funding": current_funding,
                        "prev_funding": prev_funding,
                        "current_close": closes[-1],
                        "prev_confirm_close": closes[0],
                    }
                )

        # Short signal: Funding flips from positive to negative
        elif prev_funding >= 0 and current_funding < -0.00001: # Small threshold to avoid noise around 0
            # Price confirmation: current close must be lower than the close 'price_confirm_period' bars ago
            if closes[-1] < closes[0]:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "funding_flip_negative_price_confirm",
                        "current_funding": current_funding,
                        "prev_funding": prev_funding,
                        "current_close": closes[-1],
                        "prev_confirm_close": closes[0],
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: You could adapt confidence or cooldown here based on trade outcome.
        # For simplicity, we'll just log.
        pass