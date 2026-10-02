from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ConnorsRsiSnapback(Strategy):
    METADATA = {
        "name": "Connors RSI Snapback",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 2
        self.oversold_thresh = 5.0
        self.overbought_thresh = 95.0
        self.long_exit_thresh = 70.0
        self.short_exit_thresh = 30.0
        self.cooldown_bars = 14
        self.last_exit_bar = -999

    def _rsi(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 10)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None:
            return None

        current_price = ctx.bar.close
        pos_dir = ctx.position_direction()

        # Handle exits for open positions
        if pos_dir == "long":
            if rsi_val >= self.long_exit_thresh:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "rsi2_mean_reversion_exit_long",
                        "rsi": round(rsi_val, 2),
                        "price": current_price,
                        "bars_held": ctx.bar_index - self.last_exit_bar,
                    },
                )
            return None

        if pos_dir == "short":
            if rsi_val <= self.short_exit_thresh:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "rsi2_mean_reversion_exit_short",
                        "rsi": round(rsi_val, 2),
                        "price": current_price,
                        "bars_held": ctx.bar_index - self.last_exit_bar,
                    },
                )
            return None

        # Hard post-exit cooldown enforcement to prevent friction churn
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Strict extreme entries
        if rsi_val <= self.oversold_thresh:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=250.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "rsi2_ultra_oversold_snapback",
                    "rsi": round(rsi_val, 2),
                    "price": current_price,
                },
            )

        if rsi_val >= self.overbought_thresh:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=250.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "rsi2_ultra_overbought_snapback",
                    "rsi": round(rsi_val, 2),
                    "price": current_price,
                },
            )

        return None