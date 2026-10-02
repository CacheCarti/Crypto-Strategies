from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcRsiSnapbackScalp(Strategy):
    METADATA = {
        "name": "BtcRsiSnapbackScalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 200.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 10
        self.oversold = 18.0
        self.overbought = 82.0
        self.sl_bps = 100.0
        self.tp_bps = 200.0
        self.max_hold_bars = 8
        self.cooldown_bars = 48
        self.last_trade_bar = -100
        self.entry_bar_idx = 0

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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar_idx

            if direction == "long" and (rsi >= 50.0 or bars_held >= self.max_hold_bars):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_snap_target_or_time_exit", "rsi": rsi, "bars_held": bars_held}
                )

            if direction == "short" and (rsi <= 50.0 or bars_held >= self.max_hold_bars):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_snap_target_or_time_exit", "rsi": rsi, "bars_held": bars_held}
                )

            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        is_green = ctx.bar.close > ctx.bar.open
        is_red = ctx.bar.close < ctx.bar.open

        if rsi < self.oversold and is_green:
            self.entry_bar_idx = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.sl_bps,
                take_profit_bps=self.tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={"reason": "extreme_oversold_snapback_long", "rsi": rsi, "close": ctx.bar.close}
            )

        if rsi > self.overbought and is_red:
            self.entry_bar_idx = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.sl_bps,
                take_profit_bps=self.tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={"reason": "extreme_overbought_snapback_short", "rsi": rsi, "close": ctx.bar.close}
            )

        return None