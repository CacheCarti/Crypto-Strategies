from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMomentumAcceleration(Strategy):
    METADATA = {
        "name": "SOL Momentum Acceleration Swing",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.window = 7
        self.min_return_pct = 0.018       # 1.8% recent move required
        self.accel_threshold = 0.012     # 1.2% acceleration spread
        self.decel_threshold = -0.004    # Exit long when deceleration drops below -0.4%
        self.cooldown_bars = 5
        self.last_exit_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        w = self.window
        needed = 2 * w + 15
        closes = ctx.closes(needed)
        if len(closes) < needed:
            return None

        highs = ctx.highs(16)
        lows = ctx.lows(16)
        atr = self._atr(highs, lows, closes[-16:], period=14)
        if atr is None or closes[-1] == 0:
            return None

        # Calculate momentum across recent window and prior window
        c_now = closes[-1]
        c_mid = closes[-(w + 1)]
        c_old = closes[-(2 * w + 1)]

        if c_mid == 0 or c_old == 0:
            return None

        ret_recent = (c_now - c_mid) / c_mid
        ret_prior = (c_mid - c_old) / c_old
        acceleration = ret_recent - ret_prior

        # Market regime check
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "meltdown_regime_exit", "regime": regime}
                )
            return None

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic based on momentum deceleration (not reversal)
        if has_pos:
            if pos_dir == "long":
                if acceleration < self.decel_threshold:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_momentum_deceleration",
                            "ret_recent": round(ret_recent, 4),
                            "ret_prior": round(ret_prior, 4),
                            "acceleration": round(acceleration, 4),
                            "price": c_now,
                        }
                    )
            elif pos_dir == "short":
                if acceleration > -self.decel_threshold:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_momentum_deceleration",
                            "ret_recent": round(ret_recent, 4),
                            "ret_prior": round(ret_prior, 4),
                            "acceleration": round(acceleration, 4),
                            "price": c_now,
                        }
                    )
            return None

        # Entry logic with mandatory cooldown
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        if bars_since_exit < self.cooldown_bars:
            return None

        # Long: strong positive momentum and accelerating upward
        if ret_recent > self.min_return_pct and acceleration > self.accel_threshold:
            confidence = min(0.9, 0.6 + abs(acceleration) * 5.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "positive_momentum_acceleration",
                    "ret_recent": round(ret_recent, 4),
                    "ret_prior": round(ret_prior, 4),
                    "acceleration": round(acceleration, 4),
                    "atr": round(atr, 4),
                    "price": c_now,
                }
            )

        # Short: strong negative momentum and accelerating downward
        if ret_recent < -self.min_return_pct and acceleration < -self.accel_threshold:
            confidence = min(0.9, 0.6 + abs(acceleration) * 5.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "negative_momentum_acceleration",
                    "ret_recent": round(ret_recent, 4),
                    "ret_prior": round(ret_prior, 4),
                    "acceleration": round(acceleration, 4),
                    "atr": round(atr, 4),
                    "price": c_now,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index