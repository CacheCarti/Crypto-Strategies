from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolRsiMeanReversion(Strategy):
    METADATA = {
        "name": "SOL RSI Mean Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 14
        self.oversold_thresh = 24.0
        self.overbought_thresh = 76.0
        self.neutral_low = 47.0
        self.neutral_high = 53.0
        self.cooldown_bars = 14
        self.last_exit_bar = -100

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
        closes = ctx.closes(self.period + 5)
        if len(closes) < self.period + 2:
            return None

        rsi_curr = self._rsi(closes, self.period)
        rsi_prev = self._rsi(closes[:-1], self.period)

        if rsi_curr is None or rsi_prev is None:
            return None

        current_price = ctx.bar.close

        # Check exit if in position
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean-reversion exit when RSI reaches the neutral 47-53 band
            if self.neutral_low <= rsi_curr <= self.neutral_high:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi_neutral_band_reversion",
                        "rsi": round(rsi_curr, 2),
                        "price": current_price,
                        "position_direction": direction,
                    },
                )
            return None

        # Hard cooldown guard after closing a position
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Setup: Deep oversold threshold with upward inflection
        if rsi_prev <= self.oversold_thresh and rsi_curr > rsi_prev:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_extreme_oversold_turn_up",
                    "rsi_curr": round(rsi_curr, 2),
                    "rsi_prev": round(rsi_prev, 2),
                    "price": current_price,
                },
            )

        # Short Setup: Deep overbought threshold with downward inflection
        if rsi_prev >= self.overbought_thresh and rsi_curr < rsi_prev:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_extreme_overbought_turn_down",
                    "rsi_curr": round(rsi_curr, 2),
                    "rsi_prev": round(rsi_prev, 2),
                    "price": current_price,
                },
            )

        return None