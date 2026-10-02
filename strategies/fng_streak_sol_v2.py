from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCapitulationStreak(Strategy):
    METADATA = {
        "name": "SOL Capitulation Streak Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 850.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_fast_period = 9
        self.ema_slow_period = 21
        self.fear_threshold = 28.0
        self.recovery_threshold = 45.0
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.fear_streak_count = 0

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

        fear_greed = float(ctx.features.get("fear_greed_index", 50.0))
        market_regime = ctx.market.get("regime", "NORMAL")

        # Track sustained fear streak duration
        if fear_greed <= self.fear_threshold:
            self.fear_streak_count += 1
        else:
            self.fear_streak_count = 0

        rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open

        # Position Management & Exit Rules
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit condition 1: Sentiment recovery target achieved with price momentum
                if fear_greed >= self.recovery_threshold and rsi >= 55.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "sentiment_recovery_target_reached",
                            "fear_greed": fear_greed,
                            "rsi": round(rsi, 2),
                            "close": current_close,
                            "bars_held": ctx.bar_index - self.last_exit_bar,
                        },
                    )

                # Exit condition 2: Technical exhaustion / overbought blow-off
                if rsi >= 74.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.85,
                        metadata={
                            "reason": "rsi_overbought_exhaustion_exit",
                            "fear_greed": fear_greed,
                            "rsi": round(rsi, 2),
                            "close": current_close,
                        },
                    )
            return None

        # Mandatory cooldown guard between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Filter out severe unorderly meltdowns
        if market_regime == "MELTDOWN":
            return None

        # Long Entry Trigger:
        # Sustained depressed sentiment (fear streak) coupled with local price rejection / oversold bounce
        is_fear_streak = (fear_greed <= self.fear_threshold) or (self.fear_streak_count >= 6)
        is_oversold_bounce = (rsi <= 36.0) and (current_close > current_open)
        is_momentum_pivot = (rsi <= 42.0) and (current_close > ema_fast) and (closes[-2] <= ema_fast)

        if is_fear_streak and (is_oversold_bounce or is_momentum_pivot):
            # Scale confidence based on extreme sentiment depth
            confidence = 0.65
            if fear_greed < 20.0:
                confidence += 0.15
            if rsi < 30.0:
                confidence += 0.10
            confidence = min(0.95, confidence)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "capitulation_streak_oversold_bounce",
                    "fear_greed": fear_greed,
                    "fear_streak_bars": self.fear_streak_count,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "close": current_close,
                    "regime": market_regime,
                },
            )

        return None