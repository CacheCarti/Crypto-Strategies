from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCapitulationStreak(Strategy):
    METADATA = {
        "name": "SOL Capitulation Streak",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 800.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_fast_period = 9
        self.ema_slow_period = 21
        self.cooldown_bars = 5
        self.last_exit_bar = -999
        self.fear_streak_bars = 0
        self.last_fg = 50.0

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
        closes = ctx.closes(45)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        if fg_index <= 30.0:
            self.fear_streak_bars += 1
        else:
            self.fear_streak_bars = 0
        self.last_fg = fg_index

        rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        price = ctx.bar.close
        prev_close = closes[-2]

        if ctx.has_position():
            if fg_index >= 48.0 and (rsi > 58.0 or price > ema_slow):
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "sentiment_recovered_exit",
                        "fear_greed": fg_index,
                        "rsi": round(rsi, 2),
                        "price": price,
                        "ema_slow": round(ema_slow, 2),
                    },
                )
            if rsi >= 72.0:
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={
                        "reason": "rsi_overbought_exit",
                        "fear_greed": fg_index,
                        "rsi": round(rsi, 2),
                        "price": price,
                    },
                )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        is_green_reversal = price > prev_close and price >= ema_fast * 0.995
        capitulation_setup = (self.fear_streak_bars >= 3 or fg_index <= 25.0) and rsi < 42.0 and is_green_reversal
        deep_reversal_setup = fg_index < 42.0 and rsi < 32.0 and is_green_reversal

        if capitulation_setup or deep_reversal_setup:
            streak_bonus = min(self.fear_streak_bars * 0.05, 0.2)
            base_conf = 0.7 if capitulation_setup else 0.6
            confidence = min(1.0, base_conf + streak_bonus)
            reason = "capitulation_streak_reversal" if capitulation_setup else "deep_fear_rsi_oversold"

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "fear_greed": fg_index,
                    "fear_streak_bars": self.fear_streak_bars,
                    "rsi": round(rsi, 2),
                    "price": price,
                    "ema_fast": round(ema_fast, 2),
                },
            )

        return None