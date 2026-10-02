from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMomentumAcceleration(Strategy):
    METADATA = {
        "name": "SolMomentumAcceleration",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.window = 6
        self.return_threshold = 0.004
        self.accel_threshold = 0.003
        self.cooldown_bars = 3
        self.last_exit_bar = -100

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = self.window * 2 + 5
        closes = ctx.closes(needed_bars)
        if len(closes) < needed_bars:
            return None

        recent_close = closes[-1]
        mid_close = closes[-1 - self.window]
        past_close = closes[-1 - 2 * self.window]

        if mid_close <= 0 or past_close <= 0:
            return None

        recent_return = (recent_close - mid_close) / mid_close
        prior_return = (mid_close - past_close) / past_close
        accel = recent_return - prior_return

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit on deceleration
        if has_pos:
            if pos_dir == "long":
                if accel < -0.001 or recent_return < 0.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_momentum_deceleration",
                            "recent_return": round(recent_return, 5),
                            "prior_return": round(prior_return, 5),
                            "accel": round(accel, 5),
                            "price": round(recent_close, 2),
                        },
                    )
            elif pos_dir == "short":
                if accel > 0.001 or recent_return > 0.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_momentum_deceleration",
                            "recent_return": round(recent_return, 5),
                            "prior_return": round(prior_return, 5),
                            "accel": round(accel, 5),
                            "price": round(recent_close, 2),
                        },
                    )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Crisis avoidance
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.8:
            return None

        # Long Entry: Positive & Accelerating upward momentum
        if recent_return > self.return_threshold and accel > self.accel_threshold:
            confidence = min(0.9, 0.6 + abs(accel) * 15.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_momentum_acceleration",
                    "recent_return": round(recent_return, 5),
                    "prior_return": round(prior_return, 5),
                    "accel": round(accel, 5),
                    "price": round(recent_close, 2),
                },
            )

        # Short Entry: Negative & Accelerating downward momentum
        if recent_return < -self.return_threshold and accel < -self.accel_threshold:
            confidence = min(0.9, 0.6 + abs(accel) * 15.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_momentum_acceleration",
                    "recent_return": round(recent_return, 5),
                    "prior_return": round(prior_return, 5),
                    "accel": round(accel, 5),
                    "price": round(recent_close, 2),
                },
            )

        return None