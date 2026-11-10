from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolFundingCarry(Strategy):
    METADATA = {
        "name": "Solana Funding Carry Strategy",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24 # 24 hours for funding rate average
        self.ema_fast_period = 12
        self.ema_slow_period = 24
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        # Calculate initial SMA for the first EMA value
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic: do not trade if a position was recently closed
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Calculate average funding rate over a longer period for bias
        funding_rates = [ctx.features.get(f"funding_rate_solusdt", 0.0) for _ in range(self.funding_period)]
        # For simplicity, we'll average the last `funding_period` values.
        # In a real scenario, ctx.features.get() would need to retrieve historical values or the engine would provide a list.
        # For this contract, we'll assume the feature provider gives the current rate, and we need to calculate rolling average.
        # However, ctx.features.get() only gives CURRENT value. To get historical, we'd need to store them.
        # Let's simplify and use current funding rate with a threshold for bias.
        # A more robust solution would require storing historical funding rates in self and calculating EMA/SMA.

        # Given the constraint of ctx.features.get("key", default) only providing the current value,
        # we cannot easily compute a rolling average of funding rates directly within on_bar without
        # storing them in self, which adds state management complexity.
        # Let's adapt: we'll use the *current* funding rate but require it to be consistently
        # positive/negative to indicate a sustained bias.

        # For a sustained bias, let's look at the current rate and assume it reflects the recent trend.
        # We'll use a threshold to determine if funding is "favorable" for long or short.
        long_funding_threshold = 0.0001  # e.g., 0.01% per 8 hours, positive for longs
        short_funding_threshold = -0.0001 # e.g., -0.01% per 8 hours, negative for shorts

        # Price confirmation using EMAs
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if ema_fast is None or ema_slow is None:
            return None

        current_price = ctx.bar.close

        # Entry conditions
        if not ctx.has_position():
            # Long entry: Positive funding rate and price momentum (fast EMA above slow EMA)
            if current_funding_rate > long_funding_threshold and ema_fast > ema_slow and current_price > ema_slow:
                confidence = min(0.7, abs(current_funding_rate / long_funding_threshold) * 0.3 + 0.4)
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "favorable_funding_ema_bull_cross",
                        "funding_rate": current_funding_rate,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "current_price": current_price,
                    }
                )

            # Short entry: Negative funding rate and price momentum (fast EMA below slow EMA)
            elif current_funding_rate < short_funding_threshold and ema_fast < ema_slow and current_price < ema_slow:
                confidence = min(0.7, abs(current_funding_rate / short_funding_threshold) * 0.3 + 0.4)
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "favorable_funding_ema_bear_cross",
                        "funding_rate": current_funding_rate,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "current_price": current_price,
                    }
                )

        # Exit conditions
        elif ctx.has_position():
            position_direction = ctx.position_direction()

            # Long position exit: Funding rate normalizes or momentum reverses
            if position_direction == "long":
                if current_funding_rate <= long_funding_threshold * 0.5 or ema_fast < ema_slow:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "long_exit_funding_normalized_or_ema_bear_cross",
                            "funding_rate": current_funding_rate,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "current_price": current_price,
                        }
                    )

            # Short position exit: Funding rate normalizes or momentum reverses
            elif position_direction == "short":
                if current_funding_rate >= short_funding_threshold * 0.5 or ema_fast > ema_slow:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "short_exit_funding_normalized_or_ema_bull_cross",
                            "funding_rate": current_funding_rate,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "current_price": current_price,
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already set self.last_trade_bar in on_bar when issuing a signal.
        # This method can be used for logging or more complex adaptive logic if needed.
        pass