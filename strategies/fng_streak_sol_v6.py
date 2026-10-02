from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCapitulationStreak(Strategy):
    METADATA = {
        "name": "SolCapitulationStreak",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 850.0,
        "declared_hold_seconds": 43200,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.fear_streak_count = 0
        self.fear_threshold = 28.0
        self.recovery_threshold = 44.0
        self.prev_rsi = 50.0

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
        closes = ctx.closes(self.METADATA["warmup_bars"] + 10)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        fg_index = float(ctx.features.get("fear_greed_index", 50.0))

        # Track sustained fear streak (hourly bars where FG is depressed)
        if fg_index <= self.fear_threshold:
            self.fear_streak_count += 1
        else:
            self.fear_streak_count = max(0, self.fear_streak_count - 1)

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        if ema_fast is None or ema_slow is None:
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                # Exit when sentiment rebounds significantly or RSI reaches overbought territory
                exit_sentiment = fg_index >= self.recovery_threshold and self.fear_streak_count == 0
                exit_rsi = rsi >= 72.0
                if exit_sentiment or exit_rsi:
                    reason = "sentiment_recovery" if exit_sentiment else "rsi_overbought_take_profit"
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": reason,
                            "fear_greed": fg_index,
                            "rsi": round(rsi, 2),
                            "price": current_price,
                        },
                    )
            self.prev_rsi = rsi
            return None

        # Entry logic: Cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            self.prev_rsi = rsi
            return None

        # Setup A: Sustained Capitulation streak + RSI turning upward from oversold
        streak_reversal = (self.fear_streak_count >= 18) and (self.prev_rsi < 35.0) and (rsi >= 35.0)

        # Setup B: Deep local capitulation bounce (RSI < 28 hooking up with low FG)
        deep_dip_reversal = (fg_index < 38.0) and (self.prev_rsi < 28.0) and (rsi > self.prev_rsi) and (current_price > ctx.bar.open)

        # Setup C: Bullish momentum crossover during moderate fear/neutral sentiment
        momentum_cross = (fg_index <= 45.0) and (self.prev_rsi < 42.0) and (rsi >= 46.0) and (ema_fast > ema_slow)

        if streak_reversal or deep_dip_reversal or momentum_cross:
            confidence = 0.85 if streak_reversal else (0.75 if deep_dip_reversal else 0.65)
            reason = (
                "fear_streak_capitulation_turn"
                if streak_reversal
                else ("deep_fear_rsi_bounce" if deep_dip_reversal else "momentum_fear_recovery")
            )

            self.prev_rsi = rsi
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "fear_greed": fg_index,
                    "fear_streak_count": self.fear_streak_count,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": current_price,
                },
            )

        self.prev_rsi = rsi
        return None