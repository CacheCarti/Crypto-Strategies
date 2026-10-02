from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolMomentumAcceleration(Strategy):
    METADATA = {
        "name": "SOL Momentum Acceleration",
        "domain": "sol_usdc",
        "declared_sl_bps": 360.0,
        "declared_tp_bps": 620.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.window = 6
        self.ema_trend_period = 40
        self.vol_period = 20
        self.accel_threshold = 0.022
        self.min_return = 0.015
        self.decel_threshold = 0.012
        self.cooldown_bars = 10
        self.last_exit_bar = -100

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _calc_momentum_and_accel(self, closes: list) -> tuple:
        k = self.window
        p_now = closes[-1]
        p_mid = closes[-1 - k]
        p_old = closes[-1 - (2 * k)]

        if p_mid <= 0 or p_old <= 0:
            return 0.0, 0.0, 0.0

        ret_recent = (p_now - p_mid) / p_mid
        ret_prior = (p_mid - p_old) / p_old
        accel = ret_recent - ret_prior
        return ret_recent, ret_prior, accel

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = max(self.ema_trend_period + 5, (2 * self.window) + 1)
        closes = ctx.closes(req_bars)
        if len(closes) < req_bars:
            return None

        volumes = ctx.volumes(self.vol_period + 1)
        if len(volumes) < self.vol_period + 1:
            return None

        current_price = ctx.bar.close
        ret_recent, ret_prior, accel = self._calc_momentum_and_accel(closes)
        ema_trend = self._ema(closes, self.ema_trend_period)
        vol_avg = self._sma(volumes[:-1], self.vol_period)

        # In-position logic: check for significant deceleration or invalidation
        if ctx.has_position():
            pos_dir = ctx.position_direction()

            if pos_dir == "long":
                # Exit when momentum sharply decelerates or turns decisively negative
                if accel < -self.decel_threshold or ret_recent < -0.010:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_momentum_exhaustion",
                            "ret_recent": round(ret_recent, 5),
                            "ret_prior": round(ret_prior, 5),
                            "accel": round(accel, 5),
                            "price": current_price,
                        },
                    )

            elif pos_dir == "short":
                # Exit when downward momentum exhausts or price accelerates upwards
                if accel > self.decel_threshold or ret_recent > 0.010:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_momentum_exhaustion",
                            "ret_recent": round(ret_recent, 5),
                            "ret_prior": round(ret_prior, 5),
                            "accel": round(accel, 5),
                            "price": current_price,
                        },
                    )

            return None

        # Cooldown guard after any closed trade
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime gating
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        # Volume confirmation to filter out low-conviction drift
        current_vol = ctx.bar.volume
        vol_confirmed = vol_avg is not None and current_vol > (vol_avg * 0.9)

        if not vol_confirmed or ema_trend is None:
            return None

        # Long Entry: Strong positive return + positive acceleration + above trend EMA
        if (
            ret_recent > self.min_return
            and accel > self.accel_threshold
            and current_price > ema_trend
        ):
            confidence = min(0.9, 0.55 + (accel / 0.06))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "high_conviction_bull_acceleration",
                    "ret_recent": round(ret_recent, 5),
                    "ret_prior": round(ret_prior, 5),
                    "accel": round(accel, 5),
                    "ema_trend": round(ema_trend, 3),
                    "price": current_price,
                },
            )

        # Short Entry: Strong negative return + downward acceleration + below trend EMA
        if (
            ret_recent < -self.min_return
            and accel < -self.accel_threshold
            and current_price < ema_trend
        ):
            confidence = min(0.9, 0.55 + (abs(accel) / 0.06))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "high_conviction_bear_acceleration",
                    "ret_recent": round(ret_recent, 5),
                    "ret_prior": round(ret_prior, 5),
                    "accel": round(accel, 5),
                    "ema_trend": round(ema_trend, 3),
                    "price": current_price,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index