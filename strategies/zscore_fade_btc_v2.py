from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcZScoreReversion(Strategy):
    METADATA = {
        "name": "BTC Z-Score Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 30
        self.entry_threshold = 2.45
        self.cooldown_bars = 10
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

        z_score = self._zscore(closes, self.period)
        if z_score is None:
            return None

        current_close = ctx.bar.close

        # Position management / Mean-reversion exit logic
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and z_score >= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "zscore_reverted_to_mean_long",
                        "z_score": round(z_score, 3),
                        "close": current_close,
                    },
                )
            elif direction == "short" and z_score <= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "zscore_reverted_to_mean_short",
                        "z_score": round(z_score, 3),
                        "close": current_close,
                    },
                )
            return None

        # Hard multi-bar cooldown after previous exit to prevent friction churn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Selective high-conviction entries at extreme z-score deviations
        if z_score <= -self.entry_threshold:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "extreme_zscore_oversold_entry",
                    "z_score": round(z_score, 3),
                    "close": current_close,
                    "threshold": -self.entry_threshold,
                },
            )
        elif z_score >= self.entry_threshold:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "extreme_zscore_overbought_entry",
                    "z_score": round(z_score, 3),
                    "close": current_close,
                    "threshold": self.entry_threshold,
                },
            )

        return None