from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EmaMomentumScalp(Strategy):
    METADATA = {
        "name": "EmaMomentumScalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 140.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 9
        self.slow_period = 24
        self.cooldown_bars = 20
        self.min_spread_bps = 4.0
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 5)
        if len(closes) < self.slow_period + 2:
            return None

        # Current and previous EMAs to detect the exact cross
        fast_curr = self._ema(closes, self.fast_period)
        slow_curr = self._ema(closes, self.slow_period)
        fast_prev = self._ema(closes[:-1], self.fast_period)
        slow_prev = self._ema(closes[:-1], self.slow_period)

        if fast_curr is None or slow_curr is None or fast_prev is None or slow_prev is None:
            return None

        spread_bps = ((fast_curr - slow_curr) / slow_curr) * 10000.0
        is_long = ctx.position_direction() == "long"
        is_short = ctx.position_direction() == "short"

        # Manage open position exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Exit on opposite cross or timeout
            if is_long and fast_curr < slow_curr:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "ema_bearish_cross_exit",
                    "fast_ema": round(fast_curr, 2),
                    "slow_ema": round(slow_curr, 2),
                    "bars_held": bars_held,
                })
            elif is_short and fast_curr > slow_curr:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "ema_bullish_cross_exit",
                    "fast_ema": round(fast_curr, 2),
                    "slow_ema": round(slow_curr, 2),
                    "bars_held": bars_held,
                })
            elif bars_held >= 6:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "time_stop_exit",
                    "bars_held": bars_held,
                })
            return None

        # Entry gating
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Bullish Crossover Entry
        if fast_prev <= slow_prev and fast_curr > slow_curr and spread_bps >= self.min_spread_bps:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_bullish_crossover",
                    "fast_ema": round(fast_curr, 2),
                    "slow_ema": round(slow_curr, 2),
                    "spread_bps": round(spread_bps, 2),
                }
            )

        # Bearish Crossover Entry
        if fast_prev >= slow_prev and fast_curr < slow_curr and spread_bps <= -self.min_spread_bps:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_bearish_crossover",
                    "fast_ema": round(fast_curr, 2),
                    "slow_ema": round(slow_curr, 2),
                    "spread_bps": round(spread_bps, 2),
                }
            )

        return None