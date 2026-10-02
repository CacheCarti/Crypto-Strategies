from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolRsiMeanReversion(Strategy):
    METADATA = {
        "name": "SOL RSI Mean Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 14
        self.oversold = 26.0
        self.overbought = 74.0
        self.exit_long_rsi = 52.0
        self.exit_short_rsi = 48.0
        self.cooldown_bars = 12
        self.last_exit_bar = -999

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.period + 5)
        if len(closes) < self.period + 2:
            return None

        rsi_curr = self._rsi(closes, self.period)
        rsi_prev = self._rsi(closes[:-1], self.period)

        if rsi_curr is None or rsi_prev is None:
            return None

        # Position management / Exit logic
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and rsi_curr >= self.exit_long_rsi:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi_reverted_to_neutral_long_exit",
                        "rsi": round(rsi_curr, 2),
                        "price": ctx.bar.close,
                    },
                )
            elif direction == "short" and rsi_curr <= self.exit_short_rsi:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi_reverted_to_neutral_short_exit",
                        "rsi": round(rsi_curr, 2),
                        "price": ctx.bar.close,
                    },
                )
            return None

        # Hard cooldown guard between trades
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Strict entry conditions: cross back from extreme oversold/overbought
        if rsi_prev <= self.oversold and rsi_curr > self.oversold:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_oversold_cross_up",
                    "rsi_curr": round(rsi_curr, 2),
                    "rsi_prev": round(rsi_prev, 2),
                    "price": ctx.bar.close,
                },
            )

        if rsi_prev >= self.overbought and rsi_curr < self.overbought:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_overbought_cross_down",
                    "rsi_curr": round(rsi_curr, 2),
                    "rsi_prev": round(rsi_prev, 2),
                    "price": ctx.bar.close,
                },
            )

        return None