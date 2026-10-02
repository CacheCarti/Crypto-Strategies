from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class ZScoreReversion(Strategy):
    METADATA = {
        "name": "Z-Score Mean Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 30
        self.entry_threshold = 2.45
        self.cooldown_bars = 12
        self.last_exit_bar = -999

    def _zscore(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period:
            return None
        window = closes[-period:]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(variance)
        if std == 0.0:
            return 0.0
        return (window[-1] - mean) / std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 1)
        if len(closes) < self.period:
            return None

        z = self._zscore(closes, self.period)
        if z is None:
            return None

        price = ctx.bar.close

        # Position management: exit when z-score mean-reverts through 0.0
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and z >= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "zscore_reverted_to_mean_exit_long",
                        "zscore": round(z, 3),
                        "price": price,
                    },
                )
            elif pos_dir == "short" and z <= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "zscore_reverted_to_mean_exit_short",
                        "zscore": round(z, 3),
                        "price": price,
                    },
                )
            return None

        # Hard multi-bar cooldown guard to prevent overtrading & friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # High-conviction entry triggers on statistical extremes (> 2.45 std dev)
        if z < -self.entry_threshold:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "zscore_extreme_oversold_entry",
                    "zscore": round(z, 3),
                    "threshold": -self.entry_threshold,
                    "price": price,
                },
            )
        elif z > self.entry_threshold:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "zscore_extreme_overbought_entry",
                    "zscore": round(z, 3),
                    "threshold": self.entry_threshold,
                    "price": price,
                },
            )

        return None