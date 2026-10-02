from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRsiMeanReversion(Strategy):
    METADATA = {
        "name": "SOL RSI Mean Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 14
        self.oversold = 22.0
        self.overbought = 78.0
        self.cooldown_bars = 16
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
        if len(closes) < self.period + 3:
            return None

        rsi_curr = self._rsi(closes, self.period)
        rsi_prev = self._rsi(closes[:-1], self.period)
        rsi_prev2 = self._rsi(closes[:-2], self.period)

        if rsi_curr is None or rsi_prev is None or rsi_prev2 is None:
            return None

        # Manage existing open positions
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and rsi_curr >= 50.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_reversion_to_neutral_exit", "rsi": rsi_curr, "price": ctx.bar.close}
                )
            elif direction == "short" and rsi_curr <= 50.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_reversion_to_neutral_exit", "rsi": rsi_curr, "price": ctx.bar.close}
                )
            return None

        # Hard cooldown enforcement between trades
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Strict mean reversion: extreme penetration followed by decisive reversal hook
        if rsi_prev2 <= self.oversold and rsi_prev < self.oversold + 3.0 and rsi_curr > rsi_prev and rsi_curr > self.oversold:
            return ctx.signal(
                "long",
                confidence=0.75,
                metadata={
                    "reason": "rsi_extreme_oversold_reversal",
                    "rsi": rsi_curr,
                    "prev_rsi": rsi_prev,
                    "price": ctx.bar.close
                }
            )

        if rsi_prev2 >= self.overbought and rsi_prev > self.overbought - 3.0 and rsi_curr < rsi_prev and rsi_curr < self.overbought:
            return ctx.signal(
                "short",
                confidence=0.75,
                metadata={
                    "reason": "rsi_extreme_overbought_reversal",
                    "rsi": rsi_curr,
                    "prev_rsi": rsi_prev,
                    "price": ctx.bar.close
                }
            )

        return None