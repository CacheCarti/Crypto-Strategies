from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcZScoreReversion(Strategy):
    METADATA = {
        "name": "BtcZScoreReversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 30
        self.entry_threshold = 2.45
        self.cooldown_bars = 12
        self.last_exit_bar = -100

    def _calc_zscore(self, closes):
        if len(closes) < self.period:
            return None, None, None
        window = closes[-self.period:]
        mean = sum(window) / self.period
        variance = sum((x - mean) ** 2 for x in window) / self.period
        std = math.sqrt(variance)
        if std == 0:
            return 0.0, mean, std
        z = (closes[-1] - mean) / std
        return z, mean, std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 5)
        if len(closes) < self.period:
            return None

        z, mean, std = self._calc_zscore(closes)
        if z is None:
            return None

        current_price = ctx.bar.close

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and z >= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "zscore_mean_reverted_long_exit",
                        "zscore": round(z, 3),
                        "mean": round(mean, 2),
                        "std": round(std, 2),
                        "price": current_price,
                    },
                )
            elif pos_dir == "short" and z <= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "zscore_mean_reverted_short_exit",
                        "zscore": round(z, 3),
                        "mean": round(mean, 2),
                        "std": round(std, 2),
                        "price": current_price,
                    },
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if z <= -self.entry_threshold:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=300.0,
                take_profit_bps=550.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "zscore_deep_oversold_entry",
                    "zscore": round(z, 3),
                    "mean": round(mean, 2),
                    "std": round(std, 2),
                    "price": current_price,
                },
            )
        elif z >= self.entry_threshold:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=300.0,
                take_profit_bps=550.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "zscore_deep_overbought_entry",
                    "zscore": round(z, 3),
                    "mean": round(mean, 2),
                    "std": round(std, 2),
                    "price": current_price,
                },
            )

        return None