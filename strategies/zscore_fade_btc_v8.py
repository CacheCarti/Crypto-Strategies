from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcZScoreReversion(Strategy):
    METADATA = {
        "name": "BTC Z-Score Mean Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 28
        self.entry_threshold = 2.40
        self.cooldown_bars = 12
        self.last_exit_bar = -100

    def _zscore(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        std = math.sqrt(variance)
        if std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 1)
        if len(closes) < self.period:
            return None

        z = self._zscore(closes, self.period)
        if z is None:
            return None

        # Position Management & Exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and z >= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "zscore_mean_reversion_long_exit",
                        "zscore": round(z, 3),
                        "price": ctx.bar.close,
                    },
                )
            elif direction == "short" and z <= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "zscore_mean_reversion_short_exit",
                        "zscore": round(z, 3),
                        "price": ctx.bar.close,
                    },
                )
            return None

        # Strict multi-bar cooldown guard after exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry Signals with tightened statistical thresholds
        if z <= -self.entry_threshold:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=250.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "zscore_deep_oversold_reversal",
                    "zscore": round(z, 3),
                    "threshold": -self.entry_threshold,
                    "price": ctx.bar.close,
                },
            )
        elif z >= self.entry_threshold:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=250.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "zscore_deep_overbought_reversal",
                    "zscore": round(z, 3),
                    "threshold": self.entry_threshold,
                    "price": ctx.bar.close,
                },
            )

        return None