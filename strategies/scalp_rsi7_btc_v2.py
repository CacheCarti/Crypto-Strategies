from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcRsiSnapbackScalp(Strategy):
    METADATA = {
        "name": "BtcRsiSnapbackScalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 125.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 8
        self.oversold = 22.0
        self.overbought = 78.0
        self.exit_lower = 45.0
        self.exit_upper = 55.0
        self.cooldown_bars = 24
        self.last_trade_bar = -100
        self.entry_bar = -100
        self.max_hold_bars = 6

    def _rsi(self, closes, period):
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
        closes = ctx.closes(self.rsi_period + 2)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        is_green = ctx.bar.close > ctx.bar.open
        is_red = ctx.bar.close < ctx.bar.open

        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            if direction == "long":
                if rsi >= self.exit_lower or bars_held >= self.max_hold_bars:
                    self.last_trade_bar = ctx.bar_index
                    reason_str = "rsi_snapback_target_reached" if rsi >= self.exit_lower else "time_stop"
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": reason_str,
                            "rsi": round(rsi, 2),
                            "bars_held": bars_held,
                        },
                    )

            elif direction == "short":
                if rsi <= self.exit_upper or bars_held >= self.max_hold_bars:
                    self.last_trade_bar = ctx.bar_index
                    reason_str = "rsi_snapback_target_reached" if rsi <= self.exit_upper else "time_stop"
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": reason_str,
                            "rsi": round(rsi, 2),
                            "bars_held": bars_held,
                        },
                    )
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        if rsi < self.oversold and is_green:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi_oversold_bullish_snapback",
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close,
                    "open": ctx.bar.open,
                },
            )

        if rsi > self.overbought and