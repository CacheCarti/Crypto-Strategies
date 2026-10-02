from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCapitulationStreak(Strategy):
    METADATA = {
        "name": "SOL Capitulation Streak Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 1100.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_threshold = 28.0
        self.recovery_threshold = 48.0
        self.rsi_period = 14
        self.zscore_period = 24
        self.cooldown_bars = 14
        self.min_fear_streak_bars = 6
        
        self.fear_streak = 0
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

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        var = sum((x - mean) ** 2 for x in slice_v) / period
        std = math.sqrt(var)
        if std == 0.0:
            return 0.0
        return (values[-1] - mean) / std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        if fg_index <= self.fear_threshold:
            self.fear_streak += 1
        else:
            self.fear_streak = 0

        rsi = self._rsi(closes, self.rsi_period)
        zscore = self._zscore(closes, self.zscore_period)
        ema9 = self._ema(closes, 9)
        ema21 = self._ema(closes, 21)

        if rsi is None or zscore is None or ema9 is None or ema21 is None:
            return None

        price = ctx.bar.close

        if ctx.has_position():
            # Exit logic: Sentiment recovery or technical overextension
            should_exit = False
            exit_reason = ""
            if fg_index >= self.recovery_threshold and rsi > 55.0:
                should_exit = True
                exit_reason = "sentiment_recovered_above_threshold"
            elif rsi >= 72.0:
                should_exit = True
                exit_reason = "rsi_overbought_profit_take"
            elif zscore > 2.2:
                should_exit = True
                exit_reason = "zscore_mean_reversion_target"

            if should_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": exit_reason,
                        "fear_greed": fg_index,
                        "rsi": round(rsi, 2),
                        "zscore": round(zscore, 2),
                        "price": round(price, 4),
                        "fear_streak": self.fear_streak,
                    },
                )
            return None

        # Entry logic: Cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Condition 1: Sustained fear capitulation streak with price momentum turning up
        streak_entry = (
            self.fear_streak >= self.min_fear_streak_bars
            and rsi < 42.0
            and closes[-1] > closes[-2]
            and zscore < -1.0
        )

        # Condition 2: Deep capitulation panic hook (extreme dip within fear regime)
        deep_panic_entry = (
            fg_index <= 32.0
            and zscore < -2.1
            and rsi < 32.0
            and price > ctx.bar.open
        )

        if streak_entry or deep_panic_entry:
            reason = "sustained_fear_streak_reversal" if streak_entry else "deep_panic_capitulation_hook"
            
            # Confidence scaling based on depth of depression
            base_conf = 0.60
            if fg_index < 20.0:
                base_conf += 0.15
            if zscore < -2.0:
                base_conf += 0.10
            confidence = min(0.95, base_conf)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "fear_greed": fg_index,
                    "fear_streak": self.fear_streak,
                    "rsi": round(rsi, 2),
                    "zscore": round(zscore, 2),
                    "ema9": round(ema9, 4),
                    "ema21": round(ema21, 4),
                    "price": round(price, 4),
                },
            )

        return None