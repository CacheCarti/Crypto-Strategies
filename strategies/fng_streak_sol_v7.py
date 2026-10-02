from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCapitulationStreak(Strategy):
    METADATA = {
        "name": "SOL Capitulation Streak Reversal",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 900.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_threshold = 28.0
        self.recovery_threshold = 44.0
        self.min_streak_bars = 16
        self.fear_streak = 0
        self.cooldown_bars = 14
        self.last_exit_bar = -100
        self.last_entry_bar = -100

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
        closes = ctx.closes(40)
        if len(closes) < 30:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))

        if fg_index <= self.fear_threshold:
            self.fear_streak += 1
        else:
            self.fear_streak = 0

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        ema9 = self._ema(closes, 9)
        prev_ema9 = self._ema(closes[:-1], 9)
        current_close = closes[-1]
        prev_close = closes[-2]

        if ctx.has_position():
            if ctx.position_direction() == "long":
                if fg_index >= self.recovery_threshold or rsi >= 70.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "sentiment_recovered_or_rsi_overbought",
                            "fear_greed": fg_index,
                            "rsi": round(rsi, 2),
                            "fear_streak": self.fear_streak,
                        },
                    )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if ctx.bar_index - self.last_entry_bar < self.cooldown_bars:
            return None

        if self.fear_streak >= self.min_streak_bars:
            ema_reclaim = (
                ema9 is not None
                and prev_ema9 is not None
                and prev_close <= prev_ema9
                and current_close > ema9
                and rsi < 48.0
            )
            oversold_bounce = rsi < 32.0 and current_close > prev_close

            if ema_reclaim or oversold_bounce:
                confidence = 0.65
                if fg_index < 20.0:
                    confidence += 0.15
                if rsi < 30.0:
                    confidence += 0.10
                confidence = min(0.95, confidence)

                trigger_reason = (
                    "fear_streak_ema_reclaim" if ema_reclaim else "fear_streak_rsi_oversold_bounce"
                )
                self.last_entry_bar = ctx.bar_index

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=450.0,
                    take_profit_bps=900.0,
                    horizon_seconds=86400,
                    metadata={
                        "reason": trigger_reason,
                        "fear_greed": fg_index,
                        "fear_streak": self.fear_streak,
                        "rsi": round(rsi, 2),
                        "close": current_close,
                        "ema9": round(ema9, 2) if ema9 else 0.0,
                    },
                )

        return None