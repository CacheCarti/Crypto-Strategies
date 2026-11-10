from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingRateMomentum(Strategy):
    METADATA = {
        "name": "Funding Rate Momentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.funding_rate_threshold_long = -0.00005 # Negative funding indicates short interest, good for longs
        self.funding_rate_threshold_short = 0.00005 # Positive funding indicates long interest, good for shorts
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trading

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
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        current_price = ctx.bar.close

        # Long Entry Condition:
        # 1. Price is above slow EMA (uptrend confirmation)
        # 2. Fast EMA is above Slow EMA (momentum confirmation)
        # 3. Funding rate is significantly negative (short squeeze potential / demand for shorts)
        if (not ctx.has_position() and
            current_price > slow_ema and
            fast_ema > slow_ema and
            current_funding_rate < self.funding_rate_threshold_long):

            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                metadata={
                    "reason": "funding_rate_driven_long_entry",
                    "current_price": current_price,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "funding_rate": current_funding_rate,
                    "funding_threshold": self.funding_rate_threshold_long
                }
            )

        # Short Entry Condition:
        # 1. Price is below slow EMA (downtrend confirmation)
        # 2. Fast EMA is below Slow EMA (momentum confirmation)
        # 3. Funding rate is significantly positive (long squeeze potential / demand for longs)
        elif (not ctx.has_position() and
              current_price < slow_ema and
              fast_ema < slow_ema and
              current_funding_rate > self.funding_rate_threshold_short):

            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                metadata={
                    "reason": "funding_rate_driven_short_entry",
                    "current_price": current_price,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "funding_rate": current_funding_rate,
                    "funding_threshold": self.funding_rate_threshold_short
                }
            )

        # Exit Condition: Funding rate normalizes or reverses significantly
        # This acts as a carry condition normalization exit, which is the core of the strategy.
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if funding rate becomes positive or crosses neutral threshold
                if current_funding_rate > -0.00001: # funding becomes less negative or positive
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_exit_funding_normalized",
                            "current_price": current_price,
                            "entry_price": ctx.position["entry_price"],
                            "funding_rate": current_funding_rate
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit short if funding rate becomes negative or crosses neutral threshold
                if current_funding_rate < 0.00001: # funding becomes less positive or negative
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_exit_funding_normalized",
                            "current_price": current_price,
                            "entry_price": ctx.position["entry_price"],
                            "funding_rate": current_funding_rate
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis or adaptive sizing in future versions
        pass