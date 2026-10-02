from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class RsiSnapback(Strategy):
    METADATA = {
        "name": "RSI Extreme Snapback",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 4
        self.oversold_threshold = 5.0
        self.overbought_threshold = 95.0
        self.exit_long_threshold = 55.0
        self.exit_short_threshold = 45.0
        self.cooldown_bars = 14
        self.cooldown_until_bar = 0

    def _rsi(self, closes, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        if len(gains) < period:
            return None
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 10)
        if len(closes) < self.rsi_period + 1:
            return None

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        # Position Management & Exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_rsi >= self.exit_long_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi_reversion_target_reached_long",
                        "rsi": current_rsi,
                        "exit_threshold": self.exit_long_threshold,
                        "price": ctx.bar.close,
                    },
                )
            if direction == "short" and current_rsi <= self.exit_short_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi_reversion_target_reached_short",
                        "rsi": current_rsi,
                        "exit_threshold": self.exit_short_threshold,
                        "price": ctx.bar.close,
                    },
                )
            return None

        # Hard Cooldown Check after exit
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Tightly filtered extreme extension entries
        if current_rsi <= self.oversold_threshold:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_extreme_oversold_snapback",
                    "rsi": current_rsi,
                    "threshold": self.oversold_threshold,
                    "price": ctx.bar.close,
                },
            )

        if current_rsi >= self.overbought_threshold:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_extreme_overbought_snapback",
                    "rsi": current_rsi,
                    "threshold": self.overbought_threshold,
                    "price": ctx.bar.close,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + self.cooldown_bars