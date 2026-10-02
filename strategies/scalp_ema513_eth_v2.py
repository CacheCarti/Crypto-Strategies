from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FastMomentumScalp(Strategy):
    METADATA = {
        "name": "Fast Momentum Scalp Variant",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 200.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 8
        self.slow_period = 21
        self.cooldown_bars = 18
        self.min_spread_pct = 0.0004
        self.last_exit_bar = -100
        self.last_entry_bar = -100

    def _ema(self, values, period: int) -> Optional[float]:
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

        fast_curr = self._ema(closes, self.fast_period)
        slow_curr = self._ema(closes, self.slow_period)
        fast_prev = self._ema(closes[:-1], self.fast_period)
        slow_prev = self._ema(closes[:-1], self.slow_period)

        if fast_curr is None or slow_curr is None or fast_prev is None or slow_prev is None:
            return None

        current_price = ctx.bar.close

        # Position exit logic on adverse crossover
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and fast_curr < slow_curr:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "bearish_ema_cross_exit",
                        "fast_ema": round(fast_curr, 2),
                        "slow_ema": round(slow_curr, 2),
                        "price": current_price,
                    }
                )
            if direction == "short" and fast_curr > slow_curr:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "bullish_ema_cross_exit",
                        "fast_ema": round(fast_curr, 2),
                        "slow_ema": round(slow_curr, 2),
                        "price": current_price,
                    }
                )
            return None

        # Cooldown check to prevent overtrading on 5m candles
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Long entry: Fast crosses above slow with separation threshold
        bull_cross = fast_prev <= slow_prev and fast_curr > slow_curr * (1.0 + self.min_spread_pct)
        if bull_cross:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fast_ema8_cross_above_ema21",
                    "fast_ema": round(fast_curr, 2),
                    "slow_ema": round(slow_curr, 2),
                    "price": current_price,
                }
            )

        # Short entry: Fast crosses below slow with separation threshold
        bear_cross = fast_prev >= slow_prev and fast_curr < slow_curr * (1.0 - self.min_spread_pct)
        if bear_cross:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fast_ema8_cross_below_ema21",
                    "fast_ema": round(fast_curr, 2),
                    "slow_ema": round(slow_curr, 2),
                    "price": current_price,
                }
            )

        return None