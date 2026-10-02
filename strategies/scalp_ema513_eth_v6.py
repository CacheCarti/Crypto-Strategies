from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class EmaMomentumScalp(Strategy):
    METADATA = {
        "name": "EmaMomentumScalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 8
        self.slow_period = 24
        self.cooldown_bars = 20
        self.min_spread_bps = 8.0
        self.last_trade_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 5)
        if len(closes) < self.slow_period + 2:
            return None

        # Check cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate current and previous EMAs
        curr_fast = self._ema(closes, self.fast_period)
        curr_slow = self._ema(closes, self.slow_period)
        prev_fast = self._ema(closes[:-1], self.fast_period)
        prev_slow = self._ema(closes[:-1], self.slow_period)

        if curr_fast is None or curr_slow is None or prev_fast is None or prev_slow is None:
            return None

        spread_bps = (abs(curr_fast - curr_slow) / curr_slow) * 10000.0

        # Bullish Crossover
        if prev_fast <= prev_slow and curr_fast > curr_slow and spread_bps >= self.min_spread_bps:
            if ctx.has_position():
                if ctx.position_direction() == "short":
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=90.0,
                        take_profit_bps=180.0,
                        horizon_seconds=1200,
                        metadata={
                            "reason": "ema_bullish_flip",
                            "fast_ema": round(curr_fast, 2),
                            "slow_ema": round(curr_slow, 2),
                            "spread_bps": round(spread_bps, 2),
                            "price": ctx.bar.close,
                        },
                    )
            else:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=90.0,
                    take_profit_bps=180.0,
                    horizon_seconds=1200,
                    metadata={
                        "reason": "ema_bullish_cross",
                        "fast_ema": round(curr_fast, 2),
                        "slow_ema": round(curr_slow, 2),
                        "spread_bps": round(spread_bps, 2),
                        "price": ctx.bar.close,
                    },
                )

        # Bearish Crossover
        if prev_fast >= prev_slow and curr_fast < curr_slow and spread_bps >= self.min_spread_bps:
            if ctx.has_position():
                if ctx.position_direction() == "long":
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=90.0,
                        take_profit_bps=180.0,
                        horizon_seconds=1200,
                        metadata={
                            "reason": "ema_bearish_flip",
                            "fast_ema": round(curr_fast, 2),
                            "slow_ema": round(curr_slow, 2),
                            "spread_bps": round(spread_bps, 2),
                            "price": ctx.bar.close,
                        },
                    )
            else:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=90.0,
                    take_profit_bps=180.0,
                    horizon_seconds=1200,
                    metadata={
                        "reason": "ema_bearish_cross",
                        "fast_ema": round(curr_fast, 2),
                        "slow_ema": round(curr_slow, 2),
                        "spread_bps": round(spread_bps, 2),
                        "price": ctx.bar.close,
                    },
                )

        return None