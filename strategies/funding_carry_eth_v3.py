from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingCarry(Strategy):
    METADATA = {
        "name": "ETH Funding Carry Strategy",
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
        self.funding_threshold_long = -0.0001  # Negative funding (longs pay shorts)
        self.funding_threshold_short = 0.0001  # Positive funding (shorts pay longs)
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars - 1 # Ensure immediate trading possible at start

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period)
        if len(closes) < self.slow_ema_period:
            return None

        current_close = ctx.bar.close
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # --- Position Entry Logic ---
        if not ctx.has_position():
            # Long entry conditions:
            # 1. Fast EMA crosses above Slow EMA (bullish momentum confirmation)
            # 2. ETH funding rate is sufficiently negative (longs receive funding)
            # 3. Price is above both EMAs
            if (fast_ema > slow_ema and closes[-2] <= slow_ema and # Cross over
                funding_rate < self.funding_threshold_long and
                current_close > fast_ema and current_close > slow_ema):
                
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "ema_crossover_funding_carry_long",
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "funding_rate": funding_rate,
                        "current_close": current_close,
                    }
                )

            # Short entry conditions:
            # 1. Fast EMA crosses below Slow EMA (bearish momentum confirmation)
            # 2. ETH funding rate is sufficiently positive (shorts receive funding)
            # 3. Price is below both EMAs
            elif (fast_ema < slow_ema and closes[-2] >= slow_ema and # Cross under
                  funding_rate > self.funding_threshold_short and
                  current_close < fast_ema and current_close < slow_ema):
                
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "ema_crossover_funding_carry_short",
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "funding_rate": funding_rate,
                        "current_close": current_close,
                    }
                )

        # --- Position Exit Logic (when in position) ---
        else:
            current_direction = ctx.position_direction()

            # Exit long if funding normalizes or momentum reverses
            if current_direction == "long":
                if funding_rate >= self.funding_threshold_long or fast_ema < slow_ema:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "funding_normalized_or_ema_reverse_exit_long",
                            "funding_rate": funding_rate,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_close": current_close,
                        }
                    )
            
            # Exit short if funding normalizes or momentum reverses
            elif current_direction == "short":
                if funding_rate <= self.funding_threshold_short or fast_ema > slow_ema:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "funding_normalized_or_ema_reverse_exit_short",
                            "funding_rate": funding_rate,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_close": current_close,
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy beyond cooldown
        pass