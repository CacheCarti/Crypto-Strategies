from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolUsSessionImpulse(Strategy):
    METADATA = {
        "name": "SOL US Session Impulse",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for one session hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13 # UTC 13:00 (9 AM ET)
        self.us_session_end_hour = 21   # UTC 21:00 (5 PM ET)
        self.entry_cooldown_bars = 5    # Cooldown after an entry attempt
        self.last_entry_bar_index = -self.entry_cooldown_bars - 1 # Initialize to allow immediate entry
        self.session_open_price = None
        self.session_high = None
        self.session_low = None
        self.session_start_bar_index = None
        self.entered_today = False
        self.ema_period = 20 # For directional confirmation

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset session data at the start of a new US session day
        if current_hour == self.us_session_start_hour and (self.session_start_bar_index is None or ctx.bar_index > self.session_start_bar_index + 1):
            if not self.entered_today: # Reset entered_today flag only at start of new day
                self.entered_today = False
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
        elif self.session_start_bar_index is not None and ctx.bar_index == self.session_start_bar_index:
            # First bar of the session, ensure session_open_price is set
            self.session_open_price = ctx.bar.open

        # Update session high/low if within the session
        if self.session_open_price is not None and self.us_session_start_hour <= current_hour < self.us_session_end_hour:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Exit logic: Close position before US session ends
        if ctx.has_position() and current_hour >= self.us_session_end_hour - 1: # Exit one hour before end
            reason = f"session_end_exit_at_hour_{current_hour}"
            return ctx.signal("flat", confidence=0.8, metadata={"reason": reason, "current_hour": current_hour})

        # Entry logic: Only consider entering during US session, and not if already entered today or on cooldown
        if (not ctx.has_position() and
            not self.entered_today and
            current_hour >= self.us_session_start_hour + 1 and # Wait at least one hour into session
            current_hour < self.us_session_end_hour - 1 and # Don't enter too close to session end
            ctx.bar_index >= self.last_entry_bar_index + self.entry_cooldown_bars):

            if self.session_open_price is None or self.session_high is None or self.session_low is None:
                return None # Not enough session data yet

            # Calculate EMA for directional confirmation
            ema = self._ema(closes, self.ema_period)
            if ema is None:
                return None

            # Determine session impulse
            long_impulse_trigger = self.session_high > self.session_open_price
            short_impulse_trigger = self.session_low < self.session_open_price

            # Look for follow-through after the initial impulse
            # Entry condition: Price breaks above session high AND is above EMA
            if long_impulse_trigger and ctx.bar.close > self.session_high and ctx.bar.close > ema:
                self.entered_today = True
                self.last_entry_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "us_session_long_impulse_breakout_ema_confirm",
                        "session_open": self.session_open_price,
                        "session_high": self.session_high,
                        "current_close": ctx.bar.close,
                        "current_ema": ema,
                        "entry_hour": current_hour
                    }
                )

            # Entry condition: Price breaks below session low AND is below EMA
            if short_impulse_trigger and ctx.bar.close < self.session_low and ctx.bar.close < ema:
                self.entered_today = True
                self.last_entry_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "us_session_short_impulse_breakdown_ema_confirm",
                        "session_open": self.session_open_price,
                        "session_low": self.session_low,
                        "current_close": ctx.bar.close,
                        "current_ema": ema,
                        "entry_hour": current_hour
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset the entered_today flag if the position was closed for any reason
        # This allows for a new entry on the next day's session
        self.entered_today = False
        # Log closing details for analysis
        # print(f"Position closed: {position['direction']} at {position['exit_price']} (Entry: {position['entry_price']})")
        # print(f"Return: {position['return_bps']} bps, Held for: {position['bars_held']} bars.")