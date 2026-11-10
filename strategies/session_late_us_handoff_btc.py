from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcLateUsSessionHandoff(Strategy):
    METADATA = {
        "name": "BTC Late US Session Handoff",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Roughly 6 hours
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this session-based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_length = 6 # hours for late US session
        self.entry_cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.entry_cooldown_bars

        # Define session boundaries
        self.late_us_session_start_hour = 18 # UTC 18:00
        self.late_us_session_end_hour = 24 # UTC 00:00 (end of day)

        # Indicator periods
        self.ema_short_period = 10
        self.ema_long_period = 30
        self.momentum_period = 5 # For measuring session impulse

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

        # Exit logic for existing position
        if ctx.has_position():
            # Exit if we are past the session handoff (UTC 00:00)
            # or if it's the beginning of the next session (UTC 01:00)
            # and we haven't exited yet.
            # We want to close before 00:00 UTC ideally, but allow for a buffer
            # to capture follow-through. Let's say we close by 01:00 UTC at the latest.
            if current_hour == 1 and ctx.bar_index > self.last_trade_bar + self.session_length:
                # Ensure we don't exit prematurely if the position was just opened
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_handoff_exit", "exit_hour": current_hour})
            return None # Hold position if within session or waiting for handoff

        # Entry logic
        # Only consider entries during the late US session
        if not (self.late_us_session_start_hour <= current_hour < self.late_us_session_end_hour):
            return None

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.entry_cooldown_bars:
            return None

        # Ensure we only try to enter once per session
        # This prevents multiple entry attempts within the same session after a cooldown
        if self.last_trade_bar >= (ctx.bar_index - (current_hour - self.late_us_session_start_hour)):
             return None


        # Calculate EMA for trend confirmation
        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)

        if ema_short is None or ema_long is None:
            return None

        # Measure session directional impulse
        # We need enough bars from the start of the session to measure impulse
        # Let's say we need at least self.momentum_period bars into the session
        bars_into_session = current_hour - self.late_us_session_start_hour
        if bars_into_session < self.momentum_period:
            return None

        # Get closes for the current session up to now
        session_closes = ctx.closes(bars_into_session + 1)[-(bars_into_session + 1):]
        if len(session_closes) < self.momentum_period + 1:
            return None

        # Calculate momentum as the percentage change over the last momentum_period bars
        momentum_start_price = session_closes[-(self.momentum_period + 1)]
        momentum_end_price = session_closes[-1]

        if momentum_start_price == 0: # Avoid division by zero
            return None

        session_momentum_pct = ((momentum_end_price - momentum_start_price) / momentum_start_price) * 100

        # Define thresholds for impulse
        long_impulse_threshold = 0.25 # 0.25% positive move in momentum_period bars
        short_impulse_threshold = -0.25 # -0.25% negative move

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Long entry conditions:
        # 1. Positive session impulse
        # 2. Price above short EMA and short EMA above long EMA (bullish trend)
        # 3. Close price is higher than the open price of the current bar (confirmation)
        if session_momentum_pct > long_impulse_threshold and \
           ctx.bar.close > ema_short and \
           ema_short > ema_long and \
           ctx.bar.close > ctx.bar.open:
            signal_direction = "long"
            confidence = min(0.7 + (session_momentum_pct / 1.0), 0.9) # Scale confidence with momentum
            reason = "bullish_session_impulse_ema_confirm"

        # Short entry conditions:
        # 1. Negative session impulse
        # 2. Price below short EMA and short EMA below long EMA (bearish trend)
        # 3. Close price is lower than the open price of the current bar (confirmation)
        elif session_momentum_pct < short_impulse_threshold and \
             ctx.bar.close < ema_short and \
             ema_short < ema_long and \
             ctx.bar.close < ctx.bar.open:
            signal_direction = "short"
            confidence = min(0.7 + (abs(session_momentum_pct) / 1.0), 0.9) # Scale confidence with momentum
            reason = "bearish_session_impulse_ema_confirm"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "session_momentum_pct": round(session_momentum_pct, 2),
                    "ema_short": round(ema_short, 2),
                    "ema_long": round(ema_long, 2),
                    "close": round(ctx.bar.close, 2)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis, reset any state if necessary
        pass