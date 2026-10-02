from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FearAccumulatorSwing(Strategy):
    METADATA = {
        "name": "Fear Accumulator Swing",
        "domain": "btc_usdc",
        "declared_sl_bps": 500.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_threshold = 32.0
        self.exit_fg_threshold = 55.0
        self.rsi_period = 14
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.rsi_entry_max = 42.0
        self.rsi_exit_min = 72.0
        self.cooldown_bars = 8
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

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        rsi_val = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if rsi_val is None or ema_fast is None or ema_slow is None:
            return None

        # Position management / Exit check
        if ctx.has_position():
            # Exit conditions: Sentiment recovery or severe technical overbought
            if fg_index >= self.exit_fg_threshold or rsi_val >= self.rsi_exit_min:
                exit_reason = (
                    "fear_greed_recovered"
                    if fg_index >= self.exit_fg_threshold
                    else "rsi_overbought_exhaustion"
                )
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": exit_reason,
                        "fear_greed_index": fg_index,
                        "rsi": rsi_val,
                        "price": current_price,
                        "bars_held": ctx.bar_index - self.last_exit_bar,
                    },
                )
            return None

        # Entry logic: Fear regime accumulation with technical dip trigger
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        in_fear_regime = fg_index <= self.fear_threshold
        technical_dip = rsi_val <= self.rsi_entry_max or current_price < ema_fast

        if in_fear_regime and technical_dip:
            # Scale confidence higher for extreme fear
            if fg_index <= 20.0:
                confidence = 0.90
            elif fg_index <= 28.0:
                confidence = 0.80
            else:
                confidence = 0.65

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_accumulation_dip_entry",
                    "fear_greed_index": fg_index,
                    "rsi": rsi_val,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": current_price,
                },
            )

        return None