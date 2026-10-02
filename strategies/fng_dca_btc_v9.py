from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFearAccumulator(Strategy):
    METADATA = {
        "name": "BTC Fear Accumulator",
        "domain": "btc_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 850.0,
        "declared_hold_seconds": 43200,
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_period = 30
        self.fear_threshold = 32.0
        self.exit_fear_threshold = 55.0
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.last_entry_bar = -100

    def _rsi(self, closes, period=14):
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

    def _ema(self, values, period):
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

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        rsi_current = self._rsi(closes, self.rsi_period)
        rsi_prev = self._rsi(closes[:-1], self.rsi_period)
        ema_val = self._ema(closes, self.ema_period)

        if rsi_current is None or rsi_prev is None or ema_val is None:
            return None

        # Manage open position
        if ctx.has_position():
            # Exit conditions: sentiment normalised or technical overbought
            if fg_index >= self.exit_fear_threshold or rsi_current >= 72.0:
                self.last_exit_bar = ctx.bar_index
                reason = "fear_greed_normalized" if fg_index >= self.exit_fear_threshold else "rsi_overbought_take_profit"
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": reason,
                        "fear_greed_index": fg_index,
                        "rsi": rsi_current,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Check cooldown period
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Entry logic: Deep sentiment discount combined with price reversal confirmation
        is_fear_zone = fg_index <= self.fear_threshold
        rsi_turning_up = (rsi_current < 42.0) and (rsi_current > rsi_prev)
        extreme_fear_discount = (fg_index <= 20.0) and (rsi_current < 35.0)

        if is_fear_zone and (rsi_turning_up or extreme_fear_discount):
            # Scale confidence based on sentiment extremity
            fear_discount_spread = max(0.0, self.fear_threshold - fg_index)
            confidence = min(0.95, 0.60 + (fear_discount_spread / 50.0))

            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_accumulation_rsi_hook",
                    "fear_greed_index": fg_index,
                    "rsi_current": rsi_current,
                    "rsi_prev": rsi_prev,
                    "ema": ema_val,
                    "close": ctx.bar.close,
                },
            )

        return None