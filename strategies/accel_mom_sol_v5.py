from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolMomentumAcceleration(Strategy):
    METADATA = {
        "name": "SolMomentumAcceleration",
        "domain": "sol_usdc",
        "declared_sl_bps": 340.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 28800,  # ~8 hours
        "warmup_bars": 55,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 8
        self.trend_ema_period = 40
        self.min_ret_threshold = 0.015       # 1.5% minimum window return
        self.accel_threshold = 0.012         # 1.2% acceleration margin
        self.decel_exit_threshold = -0.008   # Exit on substantial deceleration
        self.cooldown_bars = 10              # Strict cooldown to avoid friction churn
        self.last_exit_bar = -999

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = max((self.lookback * 2) + 15, self.trend_ema_period + 10)
        closes = ctx.closes(needed_bars)
        if len(closes) < needed_bars:
            return None

        highs = ctx.highs(needed_bars)
        lows = ctx.lows(needed_bars)

        # Baseline indicators
        trend_ema = self._ema(closes, self.trend_ema_period)
        if trend_ema is None:
            return None

        p_now = closes[-1]
        n = self.lookback
        p_mid = closes[-(n + 1)]
        p_old = closes[-(2 * n + 1)]

        if p_mid <= 0 or p_old <= 0 or p_now <= 0:
            return None

        # Momentum calculation: recent N-bar return vs prior N-bar return
        recent_ret = (p_now - p_mid) / p_mid
        prior_ret = (p_mid - p_old) / p_old
        accel = recent_ret - prior_ret

        # Volatility check
        atr = self._atr(highs, lows, closes, 14)
        atr_pct = (atr / p_now) if (atr is not None and p_now > 0) else 0.015

        # Regime safety checks
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.75:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={
                        "reason": "crisis_regime_protective_exit",
                        "market_regime": market_regime,
                        "crisis_score": round(crisis_score, 4),
                    },
                )
            return None

        # Manage active position
        if ctx.has_position():
            direction = ctx.position_direction()

            if direction == "long":
                # Exit when bullish momentum decisively breaks down or decelerates heavily
                if accel < self.decel_exit_threshold or recent_ret < -0.005:
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_momentum_exhaustion",
                            "recent_ret": round(recent_ret, 5),
                            "prior_ret": round(prior_ret, 5),
                            "accel": round(accel, 5),
                            "price": round(p_now, 4),
                        },
                    )
            elif direction == "short":
                # Exit when bearish momentum stalls or bounces
                if accel > -self.decel_exit_threshold or recent_ret > 0.005:
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_momentum_exhaustion",
                            "recent_ret": round(recent_ret, 5),
                            "prior_ret": round(prior_ret, 5),
                            "accel": round(accel, 5),
                            "price": round(p_now, 4),
                        },
                    )
            return None

        # Cooldown enforcement
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Ensure market is active enough for meaningful follow-through
        if atr_pct < 0.005:
            return None

        # High-conviction Bullish Entry:
        # 1. Price above medium trend EMA
        # 2. Strong positive recent return (>= 1.5%)
        # 3. Robust positive acceleration over prior window (>= 1.2%)
        if (
            p_now > trend_ema
            and recent_ret >= self.min_ret_threshold
            and accel >= self.accel_threshold
        ):
            confidence = min(0.95, 0.60 + (accel / (self.accel_threshold * 2.5)) * 0.35)
            return ctx.signal(
                "long",
                confidence=round(confidence, 3),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_momentum_acceleration",
                    "recent_ret": round(recent_ret, 5),
                    "prior_ret": round(prior_ret, 5),
                    "accel": round(accel, 5),
                    "trend_ema": round(trend_ema, 4),
                    "atr_pct": round(atr_pct, 5),
                },
            )

        # High-conviction Bearish Entry:
        # 1. Price below medium trend EMA
        # 2. Strong negative recent return (<= -1.5%)
        # 3. Robust downward acceleration over prior window (<= -1.2%)
        if (
            p_now < trend_ema
            and recent_ret <= -self.min_ret_threshold
            and accel <= -self.accel_threshold
        ):
            confidence = min(0.95, 0.60 + (abs(accel) / (self.accel_threshold * 2.5)) * 0.35)
            return ctx.signal(
                "short",
                confidence=round(confidence, 3),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_momentum_acceleration",
                    "recent_ret": round(recent_ret, 5),
                    "prior_ret": round(prior_ret, 5),
                    "accel": round(accel, 5),
                    "trend_ema": round(trend_ema, 4),
                    "atr_pct": round(atr_pct, 5),
                },
            )

        return None