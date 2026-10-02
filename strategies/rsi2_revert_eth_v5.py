from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class RsiSnapback(Strategy):
    METADATA = {
        "name": "RsiSnapback",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 4
        self.oversold_thresh = 6.0
        self.overbought_thresh = 94.0
        self.exit_long_thresh = 60.0
        self.exit_short_thresh = 40.0
        self.cooldown_bars = 18
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.rsi_period + 10)
        if len(closes) < self.rsi_period + 1:
            return None

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        current_price = ctx.bar.close

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_rsi >= self.exit_long_thresh:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi_long_snapback_reversion_target",
                        "rsi": round(current_rsi, 2),
                        "price": current_price,
                        "exit_bar": ctx.bar_index,
                    },
                )
            elif pos_dir == "short" and current_rsi <= self.exit_short_thresh:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi_short_snapback_reversion_target",
                        "rsi": round(current_rsi, 2),
                        "price": current_price,
                        "exit_bar": ctx.bar_index,
                    },
                )
            return None

        # Hard multi-bar cooldown after every exit to prevent overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # High-conviction extreme stretch entries
        if current_rsi <= self.oversold_thresh:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_deep_oversold_snapback",
                    "rsi": round(current_rsi, 2),
                    "threshold": self.oversold_thresh,
                    "price": current_price,
                },
            )

        if current_rsi >= self.overbought_thresh:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_deep_overbought_snapback",
                    "rsi": round(current_rsi, 2),
                    "threshold": self.overbought_thresh,
                    "price": current_price,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index