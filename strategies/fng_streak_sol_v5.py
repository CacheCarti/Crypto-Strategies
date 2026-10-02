from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCapitulationStreak(Strategy):
    METADATA = {
        "name": "SOL Capitulation Streak Reversal",
        "domain": "sol_usdc",
        "declared_sl_bps": 420.0,
        "declared_tp_bps": 780.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_threshold = 26.0
        self.recovery_threshold = 44.0
        self.min_fear_bars = 16
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.fear_streak = 0
        self.prev_rsi = 50.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        fg = float(ctx.features.get("fear_greed_index", 50.0))
        funding = float(ctx.features.get("funding_rate_solusdt", 0.0))

        if fg <= self.fear_threshold:
            self.fear_streak += 1
        else:
            self.fear_streak = 0

        rsi = self._rsi(closes, period=14)
        ema_fast = self._ema(closes, period=9)
        ema_slow = self._ema(closes, period=21)

        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        current_price = ctx.bar.close
        has_pos = ctx.has_position()

        if has_pos:
            # Check exit conditions
            sentiment_recovered = fg >= self.recovery_threshold and rsi >= 52.0
            momentum_blowoff = rsi >= 76.0
            
            if sentiment_recovered or momentum_blowoff:
                reason = "sentiment_recovery" if sentiment_recovered else "rsi_overbought_blowoff"
                self.prev_rsi = rsi
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": reason,
                        "fear_greed_index": fg,
                        "rsi": round(rsi, 2),
                        "price": round(current_price, 3),
                        "ema_fast": round(ema_fast, 3),
                    }
                )
            self.prev_rsi = rsi
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            self.prev_rsi = rsi
            return None

        # Entry logic: sustained capitulation streak + local oversold momentum reversal
        streak_valid = self.fear_streak >= self.min_fear_bars or fg < 18.0
        oversold_turning = (rsi < 42.0 and rsi > self.prev_rsi) or (self.prev_rsi < 32.0 and rsi >= 32.0)
        price_reclaiming = current_price >= ema_fast * 0.998 and funding <= 0.0003

        if streak_valid and oversold_turning and price_reclaiming:
            # Scale confidence based on extremity of fear and funding rate discount
            base_conf = 0.65
            fear_bonus = min(0.20, max(0.0, (self.fear_threshold - fg) / 40.0))
            funding_bonus = 0.08 if funding < 0.0 else 0.0
            confidence = min(0.95, base_conf + fear_bonus + funding_bonus)

            self.prev_rsi = rsi
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "capitulation_streak_oversold_reversal",
                    "fear_streak_bars": self.fear_streak,
                    "fear_greed_index": fg,
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "ema_fast": round(ema_fast, 3),
                    "ema_slow": round(ema_slow, 3),
                    "price": round(current_price, 3),
                }
            )

        self.prev_rsi = rsi
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index