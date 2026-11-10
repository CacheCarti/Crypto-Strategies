from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcLateUsSessionHandoff(Strategy):
    METADATA = {
        "name": "BTC Late US Session Handoff",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Approx 1 hour, to capture session handoff
        "warmup_bars": 50,
        "required_features": [], # No specific features required for this session-based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 18 # UTC 18:00 (late US session)
        self.entry_hour_end = 23   # UTC 23:00 (end of late US session)
        self.exit_hour = 0         # UTC 0:00 (start of next session)
        self.lookback_period = 12  # For directional impulse (12 hours)
        self.cooldown_bars = 6     # Cooldown after a trade (6 hours)
        self.last_signal_bar_index = -self.cooldown_bars

        # For faster trigger variant, track previous bar's conditions
        self.prev_close = None
        self.prev_ema_fast = None
        self.prev_ema_slow = None
        self.ema_fast_period = 5
        self.ema_slow_period = 13

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + self.ema_slow_period)
        if len(closes) < self.lookback_period + self.ema_slow_period:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # Exit logic: if a position is open, check if it's time to exit
        if ctx.has_position():
            # Exit at or before the specified exit_hour
            if current_hour == self.exit_hour and ctx.position_direction() is not None:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "session_boundary_exit",
                        "exit_hour": self.exit_hour,
                        "current_hour": current_hour,
                        "entry_price": ctx.position["entry_price"]
                    }
                )
            return None # Hold position if not exit time

        # Cooldown check
        if current_bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # Entry logic: only consider entries within the specified entry window
        if not (self.entry_hour_start <= current_hour <= self.entry_hour_end):
            self.prev_close = ctx.bar.close # Update for next bar's transition check
            return None

        # Calculate directional impulse for the late US session
        session_closes = ctx.closes(self.lookback_period)
        if len(session_closes) < self.lookback_period:
            self.prev_close = ctx.bar.close
            return None

        # Use EMA crossover for faster trigger and follow-through confirmation
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if ema_fast is None or ema_slow is None:
            self.prev_close = ctx.bar.close
            self.prev_ema_fast = ema_fast
            self.prev_ema_slow = ema_slow
            return None

        # Ensure we have previous values for transition detection
        if self.prev_ema_fast is None or self.prev_ema_slow is None:
            self.prev_ema_fast = ema_fast
            self.prev_ema_slow = ema_slow
            self.prev_close = ctx.bar.close
            return None

        # Directional impulse from the session
        # We look for a clear move in the last few bars of the entry window
        # and confirmation by EMA cross
        impulse_strength = (ctx.bar.close - session_closes[0]) / session_closes[0]

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Bullish signal: fast EMA crosses above slow EMA AND current price is above both EMAs
        if ema_fast > ema_slow and self.prev_ema_fast <= self.prev_ema_slow and ctx.bar.close > ema_fast:
            if impulse_strength > 0.005: # Require at least 0.5% upward impulse in session
                signal_direction = "long"
                confidence = min(0.7 + (impulse_strength * 10), 0.9) # Scale confidence with impulse
                reason = "ema_bull_cross_session_impulse_long"
        
        # Bearish signal: fast EMA crosses below slow EMA AND current price is below both EMAs
        elif ema_fast < ema_slow and self.prev_ema_fast >= self.prev_ema_slow and ctx.bar.close < ema_fast:
            if impulse_strength < -0.005: # Require at least 0.5% downward impulse in session
                signal_direction = "short"
                confidence = min(0.7 + (abs(impulse_strength) * 10), 0.9) # Scale confidence with impulse
                reason = "ema_bear_cross_session_impulse_short"

        # Update previous values for next bar's transition check
        self.prev_ema_fast = ema_fast
        self.prev_ema_slow = ema_slow
        self.prev_close = ctx.bar.close

        if signal_direction:
            self.last_signal_bar_index = current_bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "impulse_strength_pct": impulse_strength * 100,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": ctx.bar.close,
                    "entry_window": f"{self.entry_hour_start}-{self.entry_hour_end} UTC"
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis
        pass