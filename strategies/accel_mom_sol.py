from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class MomentumAcceleration(Strategy):
    METADATA = {
        "name": "SOL Momentum Acceleration",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.window = 6
        self.trend_period = 40
        self.accel_threshold = 0.018   # 180 bps minimum acceleration spread
        self.min_return = 0.010        # 100 bps directional return requirement
        self.cooldown_bars = 8         # Stricter cooldown to prevent overtrading
        self.last_exit_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = max(2 * self.window + 1, self.trend_period + 5)
        closes = ctx.closes(req_bars)
        if len(closes) < req_bars:
            return None

        c_curr = closes[-1]
        c_mid = closes[-(self.window + 1)]
        c_old = closes[-(2 * self.window + 1)]

        if c_mid <= 0 or c_old <= 0:
            return None

        ret_recent = (c_curr - c_mid) / c_mid
        ret_prior = (c_mid - c_old) / c_old
        accel = ret_recent - ret_prior

        ema_trend = self._ema(closes, self.trend_period)
        rsi_val = self._rsi(closes, 14) or 50.0

        # Position Management: Exit on clear momentum exhaustion or reversal
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                # Exit when recent return turns negative or severe deceleration occurs
                if ret_recent < -0.004 or accel < -0.015:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_momentum_exhaustion",
                            "ret_recent": round(ret_recent, 5),
                            "accel": round(accel, 5),
                            "price": c_curr,
                        },
                    )
            elif direction == "short":
                # Exit when recent return turns positive or upward snap occurs
                if ret_recent > 0.004 or accel > 0.015:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_momentum_exhaustion",
                            "ret_recent": round(ret_recent, 5),
                            "accel": round(accel, 5),
                            "price": c_curr,
                        },
                    )
            return None

        # Entry Filters & Cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or ctx.regime == "crisis" or crisis_score > 0.40:
            return None

        if ema_trend is None:
            return None

        # Long Entry: Strong positive acceleration aligned with higher timeframe trend
        if (
            ret_recent >= self.min_return
            and accel >= self.accel_threshold
            and c_curr > ema_trend
            and 48.0 <= rsi_val <= 75.0
        ):
            conf = min(0.90, 0.65 + min(accel / 0.05, 0.25))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=350.0,
                take_profit_bps=550.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "bullish_momentum_acceleration_trend_aligned",
                    "ret_recent": round(ret_recent, 5),
                    "ret_prior": round(ret_prior, 5),
                    "accel": round(accel, 5),
                    "rsi": round(rsi_val, 2),
                    "ema_trend": round(ema_trend, 2),
                    "price": c_curr,
                },
            )

        # Short Entry: Strong negative acceleration aligned with downward trend
        if (
            ret_recent <= -self.min_return
            and accel <= -self.accel_threshold
            and c_curr < ema_trend
            and 25.0 <= rsi_val <= 52.0
        ):
            conf = min(0.90, 0.65 + min(abs(accel) / 0.05, 0.25))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=350.0,
                take_profit_bps=550.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "bearish_momentum_acceleration_trend_aligned",
                    "ret_recent": round(ret_recent, 5),
                    "ret_prior": round(ret_prior, 5),
                    "accel": round(accel, 5),
                    "rsi": round(rsi_val, 2),
                    "ema_trend": round(ema_trend, 2),
                    "price": c_curr,
                },
            )

        return None