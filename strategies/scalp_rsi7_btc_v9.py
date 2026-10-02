from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcRsiSnapbackScalp(Strategy):
    METADATA = {
        "name": "BTC RSI Snapback Scalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 9
        self.oversold = 14.0
        self.overbought = 86.0
        self.exit_long_rsi = 52.0
        self.exit_short_rsi = 48.0
        self.cooldown_bars = 36
        self.last_exit_bar = -100

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
        closes = ctx.closes(self.period + 10)
        if len(closes) < self.period + 2:
            return None

        rsi = self._rsi(closes, self.period)
        if rsi is None:
            return None

        curr_bar = ctx.bar
        curr_close = curr_bar.close
        curr_open = curr_bar.open

        # Exit management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            should_exit = False
            exit_reason = ""

            if pos_dir == "long" and rsi >= self.exit_long_rsi:
                should_exit = True
                exit_reason = "rsi_reverted_to_mean_long"
            elif pos_dir == "short" and rsi <= self.exit_short_rsi:
                should_exit = True
                exit_reason = "rsi_reverted_to_mean_short"

            if should_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": exit_reason,
                        "rsi": round(rsi, 2),
                        "close": curr_close,
                    },
                )
            return None

        # Hard multi-bar cooldown gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long entry: deep extreme oversold snapback with decisive bullish candle
        if rsi <= self.oversold and curr_close > curr_open:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "extreme_oversold_rsi_reversal",
                    "rsi": round(rsi, 2),
                    "close": curr_close,
                    "open": curr_open,
                },
            )

        # Short entry: deep extreme overbought snapback with decisive bearish candle
        if rsi >= self.overbought and curr_close < curr_open:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "extreme_overbought_rsi_reversal",
                    "rsi": round(rsi, 2),
                    "close": curr_close,
                    "open": curr_open,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = max(self.last_exit_bar, ctx.bar_index)