from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionImpulse(Strategy):
    METADATA = {
        "name": "SOL Session Impulse",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for up to 6 hours
        "warmup_bars": 50,
        "required_features": [], # No external features used for this one
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 18 # UTC 18 (6 PM)
        self.entry_hour_end = 23   # UTC 23 (11 PM) - allowing entries up to this hour
        self.exit_hour = 0         # UTC 0 (12 AM) - exit before this hour
        self.cooldown_bars = 6     # Cooldown for 6 bars (6 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars

        self.impulse_period = 4 # Lookback for session impulse (4 hours)
        self.confirmation_period = 2 # Lookback for follow-through (2 hours)
        self.fast_ema_period = 10
        self.slow_ema_period = 20

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic
        if ctx.has_position():
            # Mandatory exit before self.exit_hour
            if current_hour == self.exit_hour and ctx.position_direction() is not None:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "session_boundary_exit",
                        "current_hour": current_hour,
                        "entry_price": ctx.position["entry_price"],
                        "current_price": ctx.bar.close,
                    },
                )
            # Trailing stop/take profit will be handled by the engine's declared SL/TP

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Entry logic
        if not ctx.has_position() and self.entry_hour_start <= current_hour <= self.entry_hour_end:
            # Calculate impulse of the session
            # We need enough bars for impulse_period + confirmation_period
            required_bars = self.impulse_period + self.confirmation_period
            if len(closes) < required_bars:
                return None

            # Calculate impulse from the last `impulse_period` bars
            impulse_closes = closes[-(self.impulse_period + self.confirmation_period):-self.confirmation_period]
            if len(impulse_closes) < self.impulse_period:
                return None
            
            impulse_start_price = impulse_closes[0]
            impulse_end_price = impulse_closes[-1]
            impulse_change = impulse_end_price - impulse_start_price

            # Calculate follow-through confirmation from the last `confirmation_period` bars
            confirmation_closes = closes[-self.confirmation_period:]
            if len(confirmation_closes) < self.confirmation_period:
                return None
            
            confirmation_start_price = confirmation_closes[0]
            confirmation_end_price = confirmation_closes[-1]
            confirmation_change = confirmation_end_price - confirmation_start_price

            # EMAs for trend confirmation
            fast_ema = self._ema(closes, self.fast_ema_period)
            slow_ema = self._ema(closes, self.slow_ema_period)

            if fast_ema is None or slow_ema is None:
                return None

            signal_direction = None
            reason = ""
            confidence = 0.0

            # Long condition: Positive impulse, positive confirmation, and fast EMA above slow EMA
            if impulse_change > 0 and confirmation_change > 0 and fast_ema > slow_ema:
                signal_direction = "long"
                reason = "session_impulse_long_confirmation_ema_bullish"
                confidence = 0.75

            # Short condition: Negative impulse, negative confirmation, and fast EMA below slow EMA
            elif impulse_change < 0 and confirmation_change < 0 and fast_ema < slow_ema:
                signal_direction = "short"
                reason = "session_impulse_short_confirmation_ema_bearish"
                confidence = 0.75

            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "current_hour": current_hour,
                        "impulse_change_pct": (impulse_change / impulse_start_price) * 100,
                        "confirmation_change_pct": (confirmation_change / confirmation_start_price) * 100,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_price": ctx.bar.close,
                    },
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We don't need to do anything specific here for this strategy,
        # but it's good practice to have the method.
        # Could be used for adaptive sizing or logging.
        pass