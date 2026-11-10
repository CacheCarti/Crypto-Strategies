from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionImpulse(Strategy):
    METADATA = {
        "name": "SOL Session Impulse",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold max
        "warmup_bars": 50,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 18 # UTC 18:00 (late US session)
        self.entry_hour_end = 23   # UTC 23:00
        self.exit_hour = 0         # UTC 0:00 (next day start)
        self.cooldown_bars = 12    # Cooldown for 12 hours after a trade
        self.last_trade_bar_index = -self.cooldown_bars

        # Indicators periods
        self.impulse_period = 10 # 10-hour impulse
        self.fast_ema_period = 5
        self.slow_ema_period = 20
        self.volume_ma_period = 20

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        volume_ma = self._sma(volumes, self.volume_ma_period)

        if fast_ema is None or slow_ema is None or volume_ma is None:
            return None

        # Calculate impulse for the last N bars
        impulse_closes = ctx.closes(self.impulse_period)
        if len(impulse_closes) < self.impulse_period:
            return None
        
        # Calculate directional impulse (e.g., % change over impulse_period)
        impulse_change = (impulse_closes[-1] - impulse_closes[0]) / impulse_closes[0]

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None # Still in cooldown

        # Exit logic: close position at exit_hour or if conditions reverse
        if ctx.has_position():
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_boundary_exit", "current_hour": current_hour})

            # Additional exit condition: EMA crossover reversal
            if ctx.position_direction() == "long" and fast_ema < slow_ema:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "ema_bearish_cross_exit", "fast_ema": fast_ema, "slow_ema": slow_ema})
            elif ctx.position_direction() == "short" and fast_ema > slow_ema:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "ema_bullish_cross_exit", "fast_ema": fast_ema, "slow_ema": slow_ema})

            return None # Hold position

        # Entry logic: Only during the defined entry session
        if self.entry_hour_start <= current_hour <= self.entry_hour_end:
            # Check for strong directional impulse and EMA confirmation
            # Long entry: positive impulse, fast EMA above slow EMA, above average volume
            if impulse_change > 0.005 and fast_ema > slow_ema and volumes[-1] > volume_ma * 1.2:
                # Ensure it's a recent EMA cross or strong trend confirmation, not just flat EMAs
                # Check if fast_ema recently crossed above slow_ema or maintained separation
                if self._ema(closes[:-1], self.fast_ema_period) < self._ema(closes[:-1], self.slow_ema_period):
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "session_impulse_long",
                            "impulse_change": impulse_change,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_volume": volumes[-1],
                            "avg_volume": volume_ma,
                            "entry_hour": current_hour
                        }
                    )

            # Short entry: negative impulse, fast EMA below slow EMA, above average volume
            elif impulse_change < -0.005 and fast_ema < slow_ema and volumes[-1] > volume_ma * 1.2:
                # Check if fast_ema recently crossed below slow_ema or maintained separation
                if self._ema(closes[:-1], self.fast_ema_period) > self._ema(closes[:-1], self.slow_ema_period):
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "session_impulse_short",
                            "impulse_change": impulse_change,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_volume": volumes[-1],
                            "avg_volume": volume_ma,
                            "entry_hour": current_hour
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar index to enable cooldown
        self.last_trade_bar_index = ctx.bar_index