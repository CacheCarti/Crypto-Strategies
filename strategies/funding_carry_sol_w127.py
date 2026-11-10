from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolFundingCarry(Strategy):
    METADATA = {
        "name": "SOL Funding Carry with Price Confirmation",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2-hour average hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24  # Lookback for average funding
        self.ema_period = 20    # EMA for price confirmation
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        ema_val = self._ema(closes, self.ema_period)
        if ema_val is None:
            return None

        # Get funding rates
        funding_rates = [ctx.features.get("funding_rate_solusdt", 0.0) for _ in range(self.funding_period)]
        
        # Funding rate is typically hourly, but we need to ensure we have enough history.
        # ctx.features.get("funding_rate_solusdt") gives the current bar's funding.
        # To get a history, we need to store it or rely on the engine providing historical feature values (which it does via ctx.features.get("key", default) for the current bar).
        # For simplicity, we'll use the current funding rate as the primary signal,
        # but for more advanced average, we would need to store past rates in `self`.
        
        current_funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Long Entry Conditions:
        # 1. Funding rate is significantly negative (we get paid to be long)
        # 2. Price is above EMA (bullish confirmation)
        if current_funding_rate < -0.00005 and current_price > ema_val and not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                metadata={
                    "reason": "negative_funding_long_ema_confirm",
                    "funding_rate": current_funding_rate,
                    "current_price": current_price,
                    "ema_val": ema_val,
                },
            )

        # Short Entry Conditions:
        # 1. Funding rate is significantly positive (we get paid to be short)
        # 2. Price is below EMA (bearish confirmation)
        elif current_funding_rate > 0.00005 and current_price < ema_val and not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                metadata={
                    "reason": "positive_funding_short_ema_confirm",
                    "funding_rate": current_funding_rate,
                    "current_price": current_price,
                    "ema_val": ema_val,
                },
            )

        # Exit Condition (for both long and short positions):
        # Funding rate normalizes (goes close to zero or flips sign)
        if ctx.has_position():
            position_direction = ctx.position_direction()
            
            if position_direction == "long" and current_funding_rate > -0.00001:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "funding_normalized_exit_long",
                        "funding_rate": current_funding_rate,
                        "current_price": current_price,
                        "entry_price": ctx.position["entry_price"],
                    },
                )
            elif position_direction == "short" and current_funding_rate < 0.00001:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "funding_normalized_exit_short",
                        "funding_rate": current_funding_rate,
                        "current_price": current_price,
                        "entry_price": ctx.position["entry_price"],
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis, no action needed for this strategy
        pass