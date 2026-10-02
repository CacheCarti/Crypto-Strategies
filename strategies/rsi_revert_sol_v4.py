from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolRsiMeanReversion(Strategy):
    METADATA = {
        "name": "SOL RSI Mean Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.oversold = 26.0
        self.overbought = 74.0
        self.neutral_low = 48.0
        self.neutral_high = 52.0
        self.cooldown_bars = 10
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _calc_rsi(self, closes: list, period: int) -> Optional[float]:
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 2:
            return None

        # Compute current and previous RSI
        rsi_curr = self._calc_rsi(closes, self.rsi_period)
        rsi_prev = self._calc_rsi(closes[:-1], self.rsi_period)

        if rsi_curr is None or rsi_prev is None:
            return None

        # 1. Manage active position exits
        if ctx.has_position():
            # Exit when price normalizes back to the tight neutral zone (with at least 2 bars held)
            if ctx.bar_index - self.entry_bar >= 2 and self.neutral_low <= rsi_curr <= self.neutral_high:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi_reverted_to_neutral",
                        "rsi": round(rsi_curr, 2),
                        "price": ctx.bar.close,
                        "bars_held": ctx.bar_index - self.entry_bar,
                    },
                )
            return None

        # 2. Strict post-exit cooldown gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # 3. Entry triggers (firing on clean threshold crossings to prevent duplicate entries)
        # Long: RSI crosses back above extreme oversold threshold
        if rsi_prev <= self.oversold and rsi_curr > self.oversold:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_oversold_cross_up",
                    "rsi": round(rsi_curr, 2),
                    "rsi_prev": round(rsi_prev, 2),
                    "oversold_threshold": self.oversold,
                    "price": ctx.bar.close,
                },
            )

        # Short: RSI crosses back below extreme overbought threshold
        if rsi_prev >= self.overbought and rsi_curr < self.overbought:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_overbought_cross_down",
                    "rsi": round(rsi_curr, 2),
                    "rsi_prev": round(rsi_prev, 2),
                    "overbought_threshold": self.overbought,
                    "price": ctx.bar.close,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index