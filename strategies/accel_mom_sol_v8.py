from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolMomentumAcceleration(Strategy):
    METADATA = {
        "name": "SolMomentumAcceleration",
        "domain": "sol_usdc",
        "declared_sl_bps": 360.0,
        "declared_tp_bps": 680.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 6
        self.accel_threshold = 0.015
        self.min_return_threshold = 0.018
        self.ema_trend_period = 48
        self.cooldown_bars = 8
        self.last_exit_bar = -999

    def _ema(self, values, period: int) -> Optional[float]:
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
        req_bars = max(self.lookback * 2 + 1, self.ema_trend_period + 5)
        closes = ctx.closes(req_bars)
        if len(closes) < req_bars:
            return None

        # Filter out extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.60 or regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_regime_exit", "crisis_score": crisis_score}
                )
            return None

        ema_trend = self._ema(closes, self.ema_trend_period)
        if ema_trend is None:
            return None

        p_now = closes[-1]
        p_mid = closes[-1 - self.lookback]
        p_past = closes[-1 - (2 * self.lookback)]

        if p_mid <= 0 or p_past <= 0:
            return None

        r_recent = (p_now - p_mid) / p_mid
        r_prior = (p_mid - p_past) / p_past
        accel = r_recent - r_prior

        pos_dir = ctx.position_direction()

        # Deceleration exit logic with hysteresis (avoids churning on minor 1-bar blips)
        if pos_dir == "long":
            if accel < -0.010 or r_recent < 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "long_momentum_deceleration_exit",
                        "r_recent": round(r_recent, 5),
                        "r_prior": round(r_prior, 5),
                        "accel": round(accel, 5),
                        "price": round(p_now, 2),
                    }
                )
            return None

        if pos_dir == "short":
            if accel > 0.010 or r_recent > 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "short_momentum_deceleration_exit",
                        "r_recent": round(r_recent, 5),
                        "r_prior": round(r_prior, 5),
                        "accel": round(accel, 5),
                        "price": round(p_now, 2),
                    }
                )
            return None

        # Cooldown guard: prevent rapid re-entries after exiting
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Trend regime alignment
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Long Entry: Positive accelerating momentum aligned above EMA trend
        if (
            r_recent > self.min_return_threshold
            and accel >= self.accel_threshold
            and p_now > ema_trend
            and trend_regime != "bear"
        ):
            confidence = min(0.90, max(0.55, 0.55 + accel * 10.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "bullish_momentum_acceleration",
                    "r_recent": round(r_recent, 5),
                    "r_prior": round(r_prior, 5),
                    "accel": round(accel, 5),
                    "ema_trend": round(ema_trend, 2),
                    "price": round(p_now, 2),
                }
            )

        # Short Entry: Negative accelerating momentum aligned below EMA trend
        if (
            r_recent < -self.min_return_threshold
            and accel <= -self.accel_threshold
            and p_now < ema_trend
            and trend_regime != "bull"
        ):
            confidence = min(0.90, max(0.55, 0.55 + abs(accel) * 10.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                metadata={
                    "reason": "bearish_momentum_acceleration",
                    "r_recent": round(r_recent, 5),
                    "r_prior": round(r_prior, 5),
                    "accel": round(accel, 5),
                    "ema_trend": round(ema_trend, 2),
                    "price": round(p_now, 2),
                }
            )

        return None