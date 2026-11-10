from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingCarryEth(Strategy):
    METADATA = {
        "name": "Funding Carry ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours, slightly longer than typical 1h bar
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.funding_threshold_long = 0.00015  # Positive funding for long carry (e.g., 0.015% per 8h)
        self.funding_threshold_short = -0.00015 # Negative funding for short carry
        self.cooldown_bars = 5 # Wait 5 bars after a trade before entering again
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade after warmup

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for the first 'period' values
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close
        
        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate EMAs
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Get funding rate
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)

        # Entry logic
        if not ctx.has_position():
            # Long entry condition:
            # 1. Fast EMA crosses above Slow EMA (bullish price confirmation)
            # 2. Current price is above both EMAs (stronger confirmation)
            # 3. Funding rate is positive and above threshold (favorable for long carry)
            if (closes[-2] < fast_ema and current_price > fast_ema and # Fast EMA cross (simplified, could be more robust)
                fast_ema > slow_ema and # Fast EMA above Slow EMA
                current_price > fast_ema and # Price above fast EMA
                funding_rate > self.funding_threshold_long):

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "funding_carry_long_entry",
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_price": current_price,
                        "funding_rate": funding_rate,
                    }
                )

            # Short entry condition:
            # 1. Fast EMA crosses below Slow EMA (bearish price confirmation)
            # 2. Current price is below both EMAs (stronger confirmation)
            # 3. Funding rate is negative and below threshold (favorable for short carry)
            elif (closes[-2] > fast_ema and current_price < fast_ema and # Fast EMA cross (simplified)
                  fast_ema < slow_ema and # Fast EMA below Slow EMA
                  current_price < fast_ema and # Price below fast EMA
                  funding_rate < self.funding_threshold_short):

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "funding_carry_short_entry",
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_price": current_price,
                        "funding_rate": funding_rate,
                    }
                )

        # Exit logic
        elif ctx.has_position():
            direction = ctx.position_direction()

            if direction == "long":
                # Exit long if:
                # 1. Fast EMA crosses below Slow EMA (trend reversal)
                # 2. Funding rate becomes unfavorable (below threshold or negative)
                if (fast_ema < slow_ema and closes[-2] > slow_ema) or \
                   (funding_rate < self.funding_threshold_long * 0.5): # Use a lower threshold for exit to avoid whipsaws

                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "funding_carry_long_exit",
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_price": current_price,
                            "funding_rate": funding_rate,
                        }
                    )

            elif direction == "short":
                # Exit short if:
                # 1. Fast EMA crosses above Slow EMA (trend reversal)
                # 2. Funding rate becomes unfavorable (above threshold or positive)
                if (fast_ema > slow_ema and closes[-2] < slow_ema) or \
                   (funding_rate > self.funding_threshold_short * 0.5): # Use a higher threshold for exit

                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "funding_carry_short_exit",
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_price": current_price,
                            "funding_rate": funding_rate,
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy, just logging
        pass