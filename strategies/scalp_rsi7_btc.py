from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcFastRsiSnapback(Strategy):
    METADATA = {
        "name": "BTC Fast RSI Snapback",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 7
        self.long_threshold = 14.0
        self.short_threshold = 86.0
        self.cooldown_bars = 36
        self.last_trade_bar = -999

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

        # Manage open position exits: return to neutral equilibrium (50)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if (pos_dir == "long" and rsi >= 50.0) or (pos_dir == "short" and rsi <= 50.0):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi7_neutral_reversion_exit",
                        "rsi": round(rsi, 2),
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Hard cooldown check to avoid friction decay
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        bar_midpoint = (ctx.bar.high + ctx.bar.low) / 2.0

        # Long Entry: Extreme oversold wash with decisive bullish reversal close
        if rsi < self.long_threshold and ctx.bar.close > ctx.bar.open and ctx.bar.close > bar_midpoint:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi7_extreme_oversold_snapback",
                    "rsi": round(rsi, 2),
                    "open": ctx.bar.open,
                    "close": ctx.bar.close,
                    "high": ctx.bar.high,
                    "low": ctx.bar.low,
                },
            )

        # Short Entry: Extreme overbought exhaustion with decisive bearish reversal close
        if rsi > self.short_threshold and ctx.bar.close < ctx.bar.open and ctx.bar.close < bar_midpoint:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rsi7_extreme_overbought_snapback",
                    "rsi": round(rsi, 2),
                    "open": ctx.bar.open,
                    "close": ctx.bar.close,
                    "high": ctx.bar.high,
                    "low": ctx.bar.low,
                },
            )

        return None