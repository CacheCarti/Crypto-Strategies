from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolRsiReversion(Strategy):
    METADATA = {
        "name": "SolRsiReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.oversold_thresh = 24.0
        self.overbought_thresh = 76.0
        self.cooldown_bars = 12
        self.last_exit_bar = -999

    def _rsi(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 2:
            return None

        rsi_curr = self._rsi(closes, self.rsi_period)
        rsi_prev = self._rsi(closes[:-1], self.rsi_period)

        if rsi_curr is None or rsi_prev is None:
            return None

        # Manage existing position: exit when RSI reaches opposite side of neutral band
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and rsi_curr >= 55.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_reversion_target_hit", "rsi": rsi_curr, "price": ctx.bar.close},
                )
            elif direction == "short" and rsi_curr <= 45.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_reversion_target_hit", "rsi": rsi_curr, "price": ctx.bar.close},
                )
            return None

        # Hard post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # High-conviction entry: recovery from extreme levels
        if rsi_prev <= self.oversold_thresh and rsi_curr > rsi_prev and rsi_curr > 25.0:
            confidence = min(1.0, max(0.5, (30.0 - rsi_prev) / 10.0 + 0.5))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_extreme_oversold_bounce",
                    "rsi": rsi_curr,
                    "rsi_prev": rsi_prev,
                    "threshold": self.oversold_thresh,
                    "price": ctx.bar.close,
                },
            )

        if rsi_prev >= self.overbought_thresh and rsi_curr < rsi_prev and rsi_curr < 75.0:
            confidence = min(1.0, max(0.5, (rsi_prev - 70.0) / 10.0 + 0.5))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_extreme_overbought_rejection",
                    "rsi": rsi_curr,
                    "rsi_prev": rsi_prev,
                    "threshold": self.overbought_thresh,
                    "price": ctx.bar.close,
                },
            )

        return None