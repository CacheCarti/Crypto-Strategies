from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcZscoreMeanReversion(Strategy):
    METADATA = {
        "name": "BTC Z-Score Mean Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 30
        self.entry_threshold = 2.45
        self.cooldown_bars = 12
        self.last_exit_bar = -999

    def _calc_zscore(self, closes: list) -> Optional[tuple]:
        if len(closes) < self.period:
            return None
        slice_c = closes[-self.period:]
        mean = sum(slice_c) / self.period
        variance = sum((x - mean) ** 2 for x in slice_c) / self.period
        std = math.sqrt(variance)
        if std < 1e-8:
            return 0.0, mean, std
        z = (slice_c[-1] - mean) / std
        return z, mean, std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 1)
        if len(closes) < self.period:
            return None

        z_res = self._calc_zscore(closes)
        if z_res is None:
            return None
        z, mean, std = z_res
        current_price = ctx.bar.close

        # Position management: exit when Z-score returns to/crosses the mean (0.0)
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and z >= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "zscore_mean_reverted_long_exit",
                        "zscore": round(z, 3),
                        "mean": round(mean, 2),
                        "std": round(std, 2),
                        "price": current_price,
                    },
                )
            elif direction == "short" and z <= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "zscore_mean_reverted_short_exit",
                        "zscore": round(z, 3),
                        "mean": round(mean, 2),
                        "std": round(std, 2),
                        "price": current_price,
                    },
                )
            return None

        # Hard cooldown enforcement to prevent churn and friction bleed
        if ctx.bar_index - self.last_exit_bar <= self.cooldown_bars:
            return None

        # Extreme oversold deviation -> Long
        if z <= -self.entry_threshold:
            confidence = min(0.9, 0.65 + abs(z - self.entry_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "zscore_extreme_oversold",
                    "zscore": round(z, 3),
                    "mean": round(mean, 2),
                    "std": round(std, 2),
                    "price": current_price,
                },
            )

        # Extreme overbought deviation -> Short
        if z >= self.entry_threshold:
            confidence = min(0.9, 0.65 + abs(z - self.entry_threshold) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "zscore_extreme_overbought",
                    "zscore": round(z, 3),
                    "mean": round(mean, 2),
                    "std": round(std, 2),
                    "price": current_price,
                },
            )

        return None