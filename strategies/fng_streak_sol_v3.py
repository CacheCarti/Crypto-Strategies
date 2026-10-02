from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CapitulationStreakReversion(Strategy):
    METADATA = {
        "name": "Capitulation Streak Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_fast_period = 9
        self.ema_slow_period = 21
        self.fear_threshold = 28.0
        self.min_fear_streak_bars = 8
        self.recovery_target = 46.0
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.fear_streak_count = 0
        self.prev_rsi = None

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / float(period)
        avg_loss = sum(losses[-period:]) / float(period)
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1.0)
        ema = sum(values[:period]) / float(period)
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

        # Track fear streak
        if fg_index <= self.fear_threshold:
            self.fear_streak_count += 1
        else:
            self.fear_streak_count = 0

        rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        rsi_turn_up = (self.prev_rsi is not None) and (rsi > self.prev_rsi)
        self.prev_rsi = rsi

        # Position Management & Exit Logic
        if ctx.has_position():
            sentiment_recovered = (fg_index >= self.recovery_target and rsi >= 55.0)
            overbought_exit = rsi >= 72.0
            trend_broken = (current_price < ema_slow and rsi < 42.0 and self.fear_streak_count == 0)

            if sentiment_recovered or overbought_exit or trend_broken:
                exit_reason = (
                    "sentiment_recovery_reached" if sentiment_recovered
                    else "rsi_overbought_take_profit" if overbought_exit
                    else "breakdown_below_ema_slow"
                )
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": exit_reason,
                        "fear_greed_index": fg_index,
                        "rsi": round(rsi, 2),
                        "price": current_price,
                        "ema_slow": round(ema_slow, 2),
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry Conditions (Capitulation streak or extreme local oversold in fear regime)
        fear_streak_setup = (
            self.fear_streak_count >= self.min_fear_streak_bars
            and rsi <= 38.0
            and rsi_turn_up
        )
        extreme_fear_bounce = (
            fg_index <= 20.0
            and rsi <= 32.0
            and current_price >= ctx.bar.open
        )
        localized_fear_dip = (
            fg_index <= 35.0
            and rsi <= 28.0
            and rsi_turn_up
        )

        if fear_streak_setup or extreme_fear_bounce or localized_fear_dip:
            confidence = 0.85 if extreme_fear_bounce else (0.80 if fear_streak_setup else 0.70)
            reason = (
                "extreme_fear_capitulation_bounce" if extreme_fear_bounce
                else "sustained_fear_streak_reversal" if fear_streak_setup
                else "localized_fear_dip_reversal"
            )

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "fear_greed_index": fg_index,
                    "fear_streak_bars": self.fear_streak_count,
                    "rsi": round(rsi, 2),
                    "price": current_price,
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                }
            )

        return None