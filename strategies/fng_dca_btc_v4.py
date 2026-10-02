from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearAccumulationSwing(Strategy):
    METADATA = {
        "name": "Fear Accumulation Swing",
        "domain": "btc_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 900.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_bars = 50
        self.rsi_period = 14
        self.ema_period = 34
        self.fear_threshold = 32.0
        self.exit_fg_threshold = 55.0
        self.rsi_entry_threshold = 40.0
        self.cooldown_bars = 8
        self.last_exit_bar = -100

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
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        current_price = ctx.bar.close
        rsi_val = self._rsi(closes, self.rsi_period)
        ema_val = self._ema(closes, self.ema_period)

        if rsi_val is None or ema_val is None:
            return None

        has_pos = ctx.has_position()

        if has_pos:
            if fg_index >= self.exit_fg_threshold or rsi_val >= 70.0:
                reason = "fear_discount_cleared" if fg_index >= self.exit_fg_threshold else "rsi_overbought_exit"
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": reason,
                        "fear_greed_index": fg_index,
                        "rsi": round(rsi_val, 2),
                        "price": current_price,
                        "ema34": round(ema_val, 2),
                    },
                )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if fg_index <= self.fear_threshold and rsi_val <= self.rsi_entry_threshold:
            fear_intensity = max(0.0, (self.fear_threshold - fg_index) / self.fear_threshold)
            rsi_intensity = max(0.0, (self.rsi_entry_threshold - rsi_val) / self.rsi_entry_threshold)
            confidence = min(0.95, max(0.55, 0.60 + 0.20 * fear_intensity + 0.15 * rsi_intensity))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_accumulation_rsi_dip",
                    "fear_greed_index": fg_index,
                    "rsi": round(rsi_val, 2),
                    "price": current_price,
                    "ema34": round(ema_val, 2),
                },
            )

        return None