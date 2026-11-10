from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingBiasMomentum(Strategy):
    METADATA = {
        "name": "Funding Bias Momentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.funding_threshold_long = -0.00005 # Negative funding means shorts pay longs, bullish bias
        self.funding_threshold_short = 0.00005 # Positive funding means longs pay shorts, bearish bias
        self.cooldown_bars = 5 # Cooldown period after a trade
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
        closes = ctx.closes(self.slow_ema_period + 1)
        if len(closes) < self.slow_ema_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        current_funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)

        if ctx.has_position():
            # Check for reversal or normalization of funding to exit
            if ctx.position_direction() == "long":
                # Exit long if funding becomes less favorable or flips positive
                if current_funding_rate > self.funding_threshold_long / 2: # Half threshold to exit sooner
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "funding_normalized_exit_long",
                        "current_funding_rate": current_funding_rate,
                        "funding_threshold_long": self.funding_threshold_long,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema
                    })
            elif ctx.position_direction() == "short":
                # Exit short if funding becomes less favorable or flips negative
                if current_funding_rate < self.funding_threshold_short / 2: # Half threshold to exit sooner
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "funding_normalized_exit_short",
                        "current_funding_rate": current_funding_rate,
                        "funding_threshold_short": self.funding_threshold_short,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema
                    })
            return None # Hold position if no exit condition met

        # Entry logic
        if not ctx.has_position():
            # Long entry condition:
            # 1. Negative funding rate (shorts pay longs)
            # 2. Fast EMA crosses above Slow EMA (bullish momentum)
            # 3. Current price is above both EMAs
            if (current_funding_rate < self.funding_threshold_long and
                closes[-2] <= slow_ema and closes[-1] > slow_ema and # Slow EMA cross confirmation
                fast_ema > slow_ema and closes[-1] > fast_ema):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.8, metadata={
                    "reason": "funding_bias_ema_bull_cross",
                    "current_funding_rate": current_funding_rate,
                    "funding_threshold_long": self.funding_threshold_long,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "price": closes[-1]
                })

            # Short entry condition:
            # 1. Positive funding rate (longs pay shorts)
            # 2. Fast EMA crosses below Slow EMA (bearish momentum)
            # 3. Current price is below both EMAs
            if (current_funding_rate > self.funding_threshold_short and
                closes[-2] >= slow_ema and closes[-1] < slow_ema and # Slow EMA cross confirmation
                fast_ema < slow_ema and closes[-1] < fast_ema):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.8, metadata={
                    "reason": "funding_bias_ema_bear_cross",
                    "current_funding_rate": current_funding_rate,
                    "funding_threshold_short": self.funding_threshold_short,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "price": closes[-1]
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic based on position close for this strategy,
        # but could be used to adjust confidence or thresholds in more advanced versions.
        pass