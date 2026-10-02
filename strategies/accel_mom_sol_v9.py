from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMomentumAcceleration(Strategy):
    METADATA = {
        "name": "SOL Momentum Acceleration Variant",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 45,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 7
        self.ema_period = 35
        self.min_accel_margin = 0.012  # 1.2% acceleration differential
        self.min_recent_return = 0.010  # 1.0% directional return
        self.cooldown_bars = 5
        self.last_exit_bar = -100

    def _ema(self, values, period: int) -> Optional[float]:
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
        needed_bars = self.lookback * 2 + self.ema_period
        closes = ctx.closes(needed_bars)
        if len(closes) < needed_bars:
            return None

        # Check market crisis regime
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "regime_crisis_exit", "regime": market_regime})
            return None

        current_close = closes[-1]
        mid_close = closes[-1 - self.lookback]
        start_close = closes[-1 - 2 * self.lookback]

        # Calculate momentum returns over consecutive windows
        ret_recent = (current_close - mid_close) / mid_close
        ret_prior = (mid_close - start_close) / start_close
        acceleration = ret_recent - ret_prior

        ema_val = self._ema(closes, self.ema_period)
        if ema_val is None:
            return None

        pos_dir = ctx.position_direction()

        # Handle active position: exit on deceleration rather than waiting for full reversal
        if pos_dir == "long":
            if acceleration <= -0.002 or ret_recent <= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_momentum_deceleration",
                        "ret_recent": round(ret_recent, 5),
                        "ret_prior": round(ret_prior, 5),
                        "acceleration": round(acceleration, 5),
                        "price": current_close,
                    },
                )
            return None

        if pos_dir == "short":
            if acceleration >= 0.002 or ret_recent >= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_momentum_deceleration",
                        "ret_recent": round(ret_recent, 5),
                        "ret_prior": round(ret_prior, 5),
                        "acceleration": round(acceleration, 5),
                        "price": current_close,
                    },
                )
            return None

        # Cooldown guard after prior trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        funding = ctx.features.get("funding_rate_solusdt", 0.0)

        # Long Entry: Positive recent momentum + strong upward acceleration + price above trend baseline
        if (
            ret_recent > self.min_recent_return
            and acceleration > self.min_accel_margin
            and current_close > ema_val
            and funding < 0.0006
        ):
            conf = min(0.9, 0.55 + abs(acceleration) * 8.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_momentum_acceleration",
                    "ret_recent": round(ret_recent, 5),
                    "ret_prior": round(ret_prior, 5),
                    "acceleration": round(acceleration, 5),
                    "ema_trend": round(ema_val, 2),
                    "price": current_close,
                },
            )

        # Short Entry: Negative recent momentum + downward acceleration + price below trend baseline
        if (
            ret_recent < -self.min_recent_return
            and acceleration < -self.min_accel_margin
            and current_close < ema_val
            and funding > -0.0006
        ):
            conf = min(0.9, 0.55 + abs(acceleration) * 8.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_momentum_acceleration",
                    "ret_recent": round(ret_recent, 5),
                    "ret_prior": round(ret_prior, 5),
                    "acceleration": round(acceleration, 5),
                    "ema_trend": round(ema_val, 2),
                    "price": current_close,
                },
            )

        return None