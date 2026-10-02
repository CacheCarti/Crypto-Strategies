from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearAccumulatorSwing(Strategy):
    METADATA = {
        "name": "FearAccumulatorSwing",
        "domain": "btc_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 900.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_threshold = 32.0
        self.greed_exit_threshold = 55.0
        self.rsi_period = 14
        self.ema_fast_period = 9
        self.ema_slow_period = 21
        self.cooldown_bars = 8
        self.last_exit_bar = -999

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
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        fng = float(ctx.features.get("fear_greed_index", 50.0))
        rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        current_price = ctx.bar.close
        has_pos = ctx.has_position()

        # Exit logic for open long position
        if has_pos:
            should_exit = False
            exit_reason = ""

            if fng >= self.greed_exit_threshold:
                should_exit = True
                exit_reason = "sentiment_recovered_greed_zone"
            elif rsi > 74.0:
                should_exit = True
                exit_reason = "technical_overbought_rsi_exhaustion"

            if should_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": exit_reason,
                        "fear_greed_index": fng,
                        "rsi": round(rsi, 2),
                        "price": current_price,
                        "ema_fast": round(ema_fast, 2),
                        "ema_slow": round(ema_slow, 2),
                    },
                )
            return None

        # Cooldown check after previous exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic: Fear regime accumulation with tactical price stabilization
        is_fear_regime = fng <= self.fear_threshold
        technical_trigger = (rsi < 44.0) and (current_price >= ema_fast * 0.995 or ema_fast > ema_slow)

        if is_fear_regime and technical_trigger:
            # Scale confidence with the intensity of fear
            fear_intensity = max(0.0, min(1.0, (self.fear_threshold - fng) / self.fear_threshold))
            confidence = min(0.95, max(0.60, 0.60 + 0.35 * fear_intensity))

            return ctx.signal(
                "long",
                confidence=round(confidence, 3),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_regime_accumulation_dip_recovery",
                    "fear_greed_index": fng,
                    "rsi": round(rsi, 2),
                    "price": current_price,
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "fear_intensity": round(fear_intensity, 3),
                },
            )

        return None