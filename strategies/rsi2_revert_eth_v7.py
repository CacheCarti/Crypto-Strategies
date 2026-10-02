from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class ConnorsRsiSnapback(Strategy):
    METADATA = {
        "name": "Connors RSI Snapback",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 25,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 2
        self.long_entry_thresh = 6.0
        self.long_exit_thresh = 70.0
        self.short_entry_thresh = 94.0
        self.short_exit_thresh = 30.0
        self.cooldown_bars = 14
        self.last_exit_bar = -999

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 10)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        current_price = ctx.bar.close

        # Position Management & Exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and rsi >= self.long_exit_thresh:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi_snapback_long_take_profit",
                        "rsi": round(rsi, 2),
                        "price": current_price,
                    },
                )
            elif pos_dir == "short" and rsi <= self.short_exit_thresh:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi_snapback_short_take_profit",
                        "rsi": round(rsi, 2),
                        "price": current_price,
                    },
                )
            return None

        # Hard cooldown check after any position closure
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Ultra-stretched entry triggers to keep trade frequency in target zone (30-80 trades)
        if rsi <= self.long_entry_thresh:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "deep_oversold_rsi2_snapback",
                    "rsi": round(rsi, 2),
                    "price": current_price,
                },
            )
        elif rsi >= self.short_entry_thresh:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "deep_overbought_rsi2_snapback",
                    "rsi": round(rsi, 2),
                    "price": current_price,
                },
            )

        return None