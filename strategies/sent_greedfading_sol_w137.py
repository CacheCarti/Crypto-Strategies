from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class ExtremeGreedFade(Strategy):
    METADATA = {
        "name": "Extreme Greed Fade (SOL)",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.momentum_period = 5
        self.cooldown_bars = 10
        self.last_signal_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _momentum(self, closes, period):
        if len(closes) < period + 1:
            return None
        return closes[-1] - closes[-(period + 1)]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + self.momentum_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        momentum = self._momentum(closes, self.momentum_period)

        if fast_ema is None or slow_ema is None or momentum is None:
            return None

        # Get Fear & Greed Index
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Entry logic: Fade rallies when in Extreme Greed and momentum is losing steam
        if not ctx.has_position():
            # Condition 1: Extreme Greed sentiment
            is_extreme_greed = fear_greed_index >= 75 # Typically 75-100 is Extreme Greed

            # Condition 2: Price is above slow EMA (rally)
            is_rally = closes[-1] > slow_ema

            # Condition 3: Fast EMA crosses below slow EMA (loss of momentum persistence)
            # We need previous bar's EMA values for a cross
            prev_closes = ctx.closes(self.slow_ema_period + self.momentum_period)
            if len(prev_closes) < self.METADATA["warmup_bars"]:
                return None
            prev_fast_ema = self._ema(prev_closes[:-1], self.fast_ema_period)
            prev_slow_ema = self._ema(prev_closes[:-1], self.slow_ema_period)

            if prev_fast_ema is not None and prev_slow_ema is not None:
                ema_cross_down = (fast_ema < slow_ema) and (prev_fast_ema >= prev_slow_ema)

                if is_extreme_greed and is_rally and ema_cross_down:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.8,
                        metadata={
                            "reason": "extreme_greed_fade_ema_cross_down",
                            "fear_greed_index": fear_greed_index,
                            "current_price": ctx.bar.close,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "momentum": momentum,
                        }
                    )
        # Exit logic: No explicit exit, rely on SL/TP or flip
        # If we had a position and conditions reversed (e.g. fear & greed drops significantly, or EMA crosses up)
        # However, for this strategy, we prioritize the initial fade and let SL/TP manage exits.
        # If we wanted to flip, we would define a "long" condition here.
        # For simplicity and to avoid overtrading, we'll let SL/TP handle position management.

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after a position closes
        self.last_signal_bar = ctx.bar_index
        # Log for analysis
        ctx.log(f"Position closed: {position['direction']} at {position['exit_price']}. Return: {position['return_bps']:.2f} bps. Held for {position['bars_held']} bars.")