from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolRsiMeanReversion(Strategy):
    METADATA = {
        "name": "SOL RSI Mean Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 16
        self.oversold = 22.0
        self.overbought = 78.0
        self.exit_neutral_low = 50.0
        self.exit_neutral_high = 50.0
        self.cooldown_bars = 12
        self.last_exit_bar = -999

    def _rsi(self, closes, period: int) -> Optional[float]:
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
        closes = ctx.closes(self.period + 4)
        if len(closes) < self.period + 2:
            return None

        rsi_curr = self._rsi(closes, self.period)
        rsi_prev = self._rsi(closes[:-1], self.period)

        if rsi_curr is None or rsi_prev is None:
            return None

        # 1. Manage Active Positions (Exit when RSI reverts past midline)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and rsi_curr >= self.exit_neutral_low:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "rsi_reversion_long_exit",
                        "rsi": round(rsi_curr, 2),
                        "rsi_prev": round(rsi_prev, 2),
                        "price": ctx.bar.close,
                    },
                )
            elif pos_dir == "short" and rsi_curr <= self.exit_neutral_high:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "rsi_reversion_short_exit",
                        "rsi": round(rsi_curr, 2),
                        "rsi_prev": round(rsi_prev, 2),
                        "price": ctx.bar.close,
                    },
                )
            return None

        # 2. Strict Hard Cooldown Gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # 3. High-Conviction Entries (Deep exhaustion hook reversals)
        if rsi_prev <= self.oversold and rsi_curr > rsi_prev:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_deep_oversold_hook_up",
                    "rsi": round(rsi_curr, 2),
                    "rsi_prev": round(rsi_prev, 2),
                    "price": ctx.bar.close,
                },
            )

        if rsi_prev >= self.overbought and rsi_curr < rsi_prev:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_deep_overbought_hook_down",
                    "rsi": round(rsi_curr, 2),
                    "rsi_prev": round(rsi_prev, 2),
                    "price": ctx.bar.close,
                },
            )

        return None