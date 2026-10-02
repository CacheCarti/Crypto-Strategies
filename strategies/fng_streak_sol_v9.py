from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCapitulationStreak(Strategy):
    METADATA = {
        "name": "SOL Capitulation Streak Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 850.0,
        "declared_hold_seconds": 72000,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_streak_threshold = 28.0
        self.fear_exit_threshold = 44.0
        self.min_fear_bars = 10
        self.cooldown_bars = 14
        
        self.fear_streak_count = 0
        self.last_exit_bar = -100
        self.rsi_period = 14
        self.ema_fast_period = 9
        self.ema_slow_period = 21

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
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        
        # Track fear streak duration
        if fg_index <= self.fear_streak_threshold:
            self.fear_streak_count += 1
        elif fg_index >= 38.0:
            self.fear_streak_count = 0

        current_price = ctx.bar.close
        rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        # Position Management & Exit Logic
        if ctx.has_position():
            # Exit on sentiment recovery with momentum or extreme overbought
            if (fg_index >= self.fear_exit_threshold and rsi >= 56.0) or rsi >= 72.0:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "sentiment_recovery_or_rsi_overbought",
                        "fear_greed_index": fg_index,
                        "rsi": round(rsi, 2),
                        "price": current_price,
                        "fear_streak_count": self.fear_streak_count,
                    },
                )
            return None

        # Entry Logic (Long-only Capitulation Reversal)
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        if bars_since_exit < self.cooldown_bars:
            return None

        # Crisis filter: avoid entering during extreme market-wide meltdown
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = float(ctx.market.get("crisis_score", 0.0))
        if regime == "MELTDOWN" or crisis_score > 0.85:
            return None

        # Price-action triggers during a sustained fear streak
        has_fear_streak = self.fear_streak_count >= self.min_fear_bars
        prev_closes = closes[:-1]
        prev_rsi = self._rsi(prev_closes, self.rsi_period)

        if prev_rsi is None:
            return None

        # Setup 1: Deep fear streak with RSI turning back up above 30
        oversold_bounce = has_fear_streak and prev_rsi < 32.0 and rsi >= 32.0 and current_price >= ema_fast * 0.985

        # Setup 2: Extended fear capitulation + fast EMA cross indicating local bottom formation
        ema_reversal = (self.fear_streak_count >= 16) and (rsi < 48.0) and (closes[-2] <= ema_fast) and (current_price > ema_fast)

        if oversold_bounce or ema_reversal:
            confidence = min(0.9, 0.6 + (0.01 * min(self.fear_streak_count, 20)))
            entry_reason = "capitulation_streak_rsi_bounce" if oversold_bounce else "capitulation_streak_ema_reclaim"

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": entry_reason,
                    "fear_greed_index": fg_index,
                    "fear_streak_bars": self.fear_streak_count,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": current_price,
                },
            )

        return None