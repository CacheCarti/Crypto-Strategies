from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcFastRsiSnapbackScalp(Strategy):
    METADATA = {
        "name": "BtcFastRsiSnapbackScalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 7
        self.oversold = 14.0
        self.overbought = 86.0
        self.neutral_low = 46.0
        self.neutral_high = 54.0
        self.max_hold_bars = 10
        self.cooldown_bars = 48
        self.last_exit_bar = -999
        self.entry_bar = -999

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
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 10)
        if len(closes) < self.period + 1:
            return None

        rsi_val = self._rsi(closes, self.period)
        if rsi_val is None:
            return None

        curr_bar = ctx.bar
        is_green = curr_bar.close > curr_bar.open
        is_red = curr_bar.close < curr_bar.open

        # Position management / Exit logic
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            reversion_exit = self.neutral_low <= rsi_val <= self.neutral_high
            time_exit = bars_held >= self.max_hold_bars

            if reversion_exit or time_exit:
                exit_reason = "rsi_neutral_reversion" if reversion_exit else "time_stop_reached"
                self.last_exit_bar = ctx.bar_index
                self.entry_bar = -999
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "rsi": round(rsi_val, 2),
                        "bars_held": bars_held,
                        "close": curr_bar.close,
                    },
                )
            return None

        # Hard multi-bar cooldown guard to prevent overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Strict extreme oversold snapback entry
        if rsi_val < self.oversold and is_green:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "rsi_extreme_oversold_snapback",
                    "rsi": round(rsi_val, 2),
                    "close": curr_bar.close,
                    "open": curr_bar.open,
                },
            )

        # Strict extreme overbought snapback entry
        if rsi_val > self.overbought and is_red:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "rsi_extreme_overbought_snapback",
                    "rsi": round(rsi_val, 2),
                    "close": curr_bar.close,
                    "open": curr_bar.open,
                },
            )

        return None