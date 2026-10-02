from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcZscoreReversion(Strategy):
    METADATA = {
        "name": "BTC Z-Score Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.entry_threshold = 2.35
        self.cooldown_bars = 10
        self.last_exit_bar = -100

    def _calc_zscore(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        if std == 0.0:
            return 0.0
        return (closes[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 5)
        if len(closes) < self.period:
            return None

        zscore = self._calc_zscore(closes, self.period)
        if zscore is None:
            return None

        current_price = ctx.bar.close

        # Position exit logic: return to mean (z-score crosses zero)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and zscore >= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "zscore_mean_reversion_exit_long",
                        "zscore": round(zscore, 3),
                        "price": current_price,
                    },
                )
            elif pos_dir == "short" and zscore <= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "zscore_mean_reversion_exit_short",
                        "zscore": round(zscore, 3),
                        "price": current_price,
                    },
                )
            return None

        # Hard cooldown guard after exits to prevent re-entering immediate chop
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # High-conviction entry logic with strict threshold
        if zscore <= -self.entry_threshold:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "zscore_oversold_extreme_entry",
                    "zscore": round(zscore, 3),
                    "threshold": -self.entry_threshold,
                    "price": current_price,
                },
            )
        elif zscore >= self.entry_threshold:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "zscore_overbought_extreme_entry",
                    "zscore": round(zscore, 3),
                    "threshold": self.entry_threshold,
                    "price": current_price,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index