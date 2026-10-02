from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EmaMomentumScalp(Strategy):
    METADATA = {
        "name": "EMA Momentum Scalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 8
        self.slow_period = 24
        self.cooldown_bars = 20
        self.min_spread_bps = 8.0
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
        self.last_entry_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 5)
        if len(closes) < self.slow_period + 2:
            return None

        prev_closes = closes[:-1]
        fast_now = self._ema(closes, self.fast_period)
        slow_now = self._ema(closes, self.slow_period)
        fast_prev = self._ema(prev_closes, self.fast_period)
        slow_prev = self._ema(prev_closes, self.slow_period)

        if fast_now is None or slow_now is None or fast_prev is None or slow_prev is None:
            return None

        spread_bps = (abs(fast_now - slow_now) / slow_now) * 10000.0
        bullish_cross = fast_prev <= slow_prev and fast_now > slow_now
        bearish_cross = fast_prev >= slow_prev and fast_now < slow_now

        # Position exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and bearish_cross:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "bearish_crossover_exit",
                        "fast_ema": round(fast_now, 2),
                        "slow_ema": round(slow_now, 2),
                        "price": ctx.bar.close,
                    },
                )
            elif pos_dir == "short" and bullish_cross:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "bullish_crossover_exit",
                        "fast_ema": round(fast_now, 2),
                        "slow_ema": round(slow_now, 2),
                        "price": ctx.bar.close,
                    },
                )
            return None

        # Entry logic with mandatory cooldown and momentum spread check
        if ctx.bar_index - self.last_entry_bar < self.cooldown_bars:
            return None

        if bullish_cross and spread_bps >= self.min_spread_bps:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_bullish_momentum_cross",
                    "fast_ema": round(fast_now, 2),
                    "slow_ema": round(slow_now, 2),
                    "spread_bps": round(spread_bps, 2),
                    "price": ctx.bar.close,
                },
            )

        if bearish_cross and spread_bps >= self.min_spread_bps:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_bearish_momentum_cross",
                    "fast_ema": round(fast_now, 2),
                    "slow_ema": round(slow_now, 2),
                    "spread_bps": round(spread_bps, 2),
                    "price": ctx.bar.close,
                },
            )

        return None