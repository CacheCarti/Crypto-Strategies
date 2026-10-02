from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FastMomentumCrossScalp(Strategy):
    METADATA = {
        "name": "FastMomentumCrossScalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 110.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 12
        self.slow_period = 42
        self.cooldown_bars = 96
        self.min_spread_bps = 8.0
        self.last_trade_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

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

        bullish_cross = fast_prev <= slow_prev and fast_curr > slow_curr
        bearish_cross = fast_prev >= slow_prev and fast_curr < slow_curr

        # Position exits on counter-crossover
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and bearish_cross:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "bearish_ema_cross_exit",
                        "fast_ema": round(fast_curr, 2),
                        "slow_ema": round(slow_curr, 2),
                        "close": ctx.bar.close,
                    },
                )
            elif pos_dir == "short" and bullish_cross:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "bullish_ema_cross_exit",
                        "fast_ema": round(fast_curr, 2),
                        "slow_ema": round(slow_curr, 2),
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Hard multi-bar cooldown check
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Filter out flat noise crossovers: require decisive spread separation
        spread_bps = (abs(fast_curr - slow_curr) / slow_curr) * 10000.0
        if spread_bps < self.min_spread_bps:
            return None

        if bullish_cross:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_ema_cross_entry",
                    "fast_ema": round(fast_curr, 2),
                    "slow_ema": round(slow_curr, 2),
                    "spread_bps": round(spread_bps, 2),
                    "close": ctx.bar.close,
                },
            )
        elif bearish_cross:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_ema_cross_entry",
                    "fast_ema": round(fast_curr, 2),
                    "slow_ema": round(slow_curr, 2),
                    "spread_bps": round(spread_bps, 2),
                    "close": ctx.bar.close,
                },
            )

        return None