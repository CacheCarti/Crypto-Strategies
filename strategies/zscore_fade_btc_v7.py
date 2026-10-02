import math
from typing import Optional, Dict, Any
from domains.strategy_contract import Strategy, BarContext, Signal

class BtcZscoreMeanReversion(Strategy):
    METADATA = {
        "name": "BTC Z-Score Mean Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 30
        self.entry_threshold = 2.60
        self.cooldown_bars = 14
        self.last_exit_bar = -999

    def _zscore(self, closes: list, period: int):
        if len(closes) < period:
            return None, None, None
        slice_vals = closes[-period:]
        mean = sum(slice_vals) / period
        variance = sum((x - mean) ** 2 for x in slice_vals) / period
        std = math.sqrt(variance)
        if std == 0.0:
            return 0.0, mean, 0.0
        z = (closes[-1] - mean) / std
        return z, mean, std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 5)
        if len(closes) < self.period:
            return None

        z, mean, std = self._zscore(closes, self.period)
        if z is None:
            return None

        current_price = ctx.bar.close

        # Exit existing position when Z-Score mean-reverts to zero or flips sign
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and z >= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "zscore_mean_reversion_exit_long",
                        "zscore": round(z, 3),
                        "mean": round(mean, 2),
                        "price": current_price,
                    }
                )
            elif direction == "short" and z <= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "zscore_mean_reversion_exit_short",
                        "zscore": round(z, 3),
                        "mean": round(mean, 2),
                        "price": current_price,
                    }
                )
            return None

        # Hard multi-bar cooldown after exit to eliminate excessive churn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Tightened entry thresholds to only capture genuine statistical extremes
        if z < -self.entry_threshold:
            confidence = min(0.9, 0.5 + abs(z) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "zscore_extreme_oversold_entry",
                    "zscore": round(z, 3),
                    "mean": round(mean, 2),
                    "std": round(std, 2),
                    "price": current_price,
                }
            )
        elif z > self.entry_threshold:
            confidence = min(0.9, 0.5 + abs(z) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "zscore_extreme_overbought_entry",
                    "zscore": round(z, 3),
                    "mean": round(mean, 2),
                    "std": round(std, 2),
                    "price": current_price,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index