from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcZscoreReversion(Strategy):
    METADATA = {
        "name": "BTC Z-Score Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 32
        self.threshold = 2.45
        self.cooldown_bars = 12
        self.last_exit_bar = -999

    def _zscore(self, values: list, period: int) -> Optional[float]:
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
                    confidence=0.75,
                    metadata={
                        "reason": "zscore_mean_reversion_exit_long",
                        "zscore": round(z, 3),
                        "price": price,
                    },
                )
            elif direction == "short" and z <= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "zscore_mean_reversion_exit_short",
                        "zscore": round(z, 3),
                        "price": price,
                    },
                )
            return None

        # Hard multi-bar cooldown after exit to eliminate excessive churn
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Selective high-conviction entries
        if z <= -self.threshold:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "zscore_deep_oversold_entry",
                    "zscore": round(z, 3),
                    "threshold": -self.threshold,
                    "price": price,
                },
            )
        elif z >= self.threshold:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "zscore_deep_overbought_entry",
                    "zscore": round(z, 3),
                    "threshold": self.threshold,
                    "price": price,
                },
            )

        return None