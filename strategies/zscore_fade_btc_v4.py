from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcZScoreReversion(Strategy):
    METADATA = {
        "name": "BTC Z-Score Mean Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 28
        self.threshold = 2.55
        self.cooldown_bars = 14
        self.last_exit_bar = -999

    def _zscore(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        std = math.sqrt(variance)
        if std == 0.0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 5)
        if len(closes) < self.period:
            return None

        z = self._zscore(closes, self.period)
        if z is None:
            return None

        price = ctx.bar.close

        # Position management / Exit check
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and z >= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "zscore_mean_reversion_zero_cross_long_exit",
                        "zscore": round(z, 3),
                        "price": price,
                    },
                )
            elif direction == "short" and z <= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "zscore_mean_reversion_zero_cross_short_exit",
                        "zscore": round(z, 3),
                        "price": price,
                    },
                )
            return None

        # Hard multi-bar cooldown guard after any exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Tightened entry conditions to eliminate overtrading and friction drag
        if z <= -self.threshold:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "zscore_deep_oversold_long_entry",
                    "zscore": round(z, 3),
                    "price": price,
                    "threshold": -self.threshold,
                },
            )

        if z >= self.threshold:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "zscore_deep_overbought_short_entry",
                    "zscore": round(z, 3),
                    "price": price,
                    "threshold": self.threshold,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index