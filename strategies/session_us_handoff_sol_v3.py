from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionHandoffSOL(Strategy):
    METADATA = {
        "name": "USSessionHandoffSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold for 1 hour bar, but capped by session end
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this session-based logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13 # UTC 1 PM
        self.us_session_end_hour = 21   # UTC 9 PM
        self.entry_cooldown_bars = 5    # Cooldown after an entry attempt
        self.last_entry_bar_index = -self.entry_cooldown_bars - 1 # Initialize to allow immediate entry

        # Indicators for directional impulse and confirmation
        self.sma_period_fast = 10
        self.sma_period_slow = 30
        self.rsi_period = 10
        self.min_rsi = 40
        self.max_rsi = 60

        self.session_impulse_direction: Optional[str] = None
        self.session_impulse_price: Optional[float] = None
        self.session_high: Optional[float] = None
        self.session_low: Optional[float] = None
        self.last_bar_hour = -1

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        # Use average of last 'period' for initial calculation
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 0.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset session state at the start of a new day (or before US session)
        if current_hour != self.last_bar_hour and current_hour == (self.us_session_start_hour - 1) % 24: # Hour before US session starts
            self.session_impulse_direction = None
            self.session_impulse_price = None
            self.session_high = None
            self.session_low = None
            # print(f"DEBUG: {ctx.bar.timestamp} - Resetting session state.")

        # Update session high/low during US session
        if self.us_session_start_hour <= current_hour < self.us_session_end_hour:
            if self.session_high is None or ctx.bar.high > self.session_high:
                self.session_high = ctx.bar.high
            if self.session_low is None or ctx.bar.low < self.session_low:
                self.session_low = ctx.bar.low
            # print(f"DEBUG: {ctx.bar.timestamp} - US session active. High: {self.session_high}, Low: {self.session_low}")


        # Logic to determine session impulse and potential entry
        if not ctx.has_position():
            # Determine session impulse at the end of the US session
            if current_hour == self.us_session_end_hour - 1 and self.session_impulse_direction is None: # One hour before session ends
                if self.session_high and self.session_low and self.session_high > self.session_low:
                    mid_price = (self.session_high + self.session_low) / 2
                    if ctx.bar.close > mid_price:
                        self.session_impulse_direction = "long"
                    elif ctx.bar.close < mid_price:
                        self.session_impulse_direction = "short"
                    self.session_impulse_price = ctx.bar.close
                    # print(f"DEBUG: {ctx.bar.timestamp} - Session impulse detected: {self.session_impulse_direction} at {self.session_impulse_price}")

            # Try to enter at session handoff, if impulse is set and conditions confirm
            if current_hour == self.us_session_end_hour: # After US session closes, check for handoff entry
                if self.session_impulse_direction and (ctx.bar_index - self.last_entry_bar_index > self.entry_cooldown_bars):
                    sma_fast = self._sma(closes, self.sma_period_fast)
                    sma_slow = self._sma(closes, self.sma_period_slow)
                    current_rsi = self._rsi(closes, self.rsi_period)

                    if sma_fast is None or sma_slow is None or current_rsi is None:
                        return None

                    if self.session_impulse_direction == "long":
                        # Confirm long entry: price above SMAs, SMAs crossed bullish, RSI not overbought
                        if ctx.bar.close > sma_fast and sma_fast > sma_slow and current_rsi < self.max_rsi:
                            self.last_entry_bar_index = ctx.bar_index
                            return ctx.signal("long", confidence=0.7,
                                              metadata={
                                                  "reason": "us_session_handoff_long_confirmation",
                                                  "session_impulse": "long",
                                                  "session_close_price": self.session_impulse_price,
                                                  "current_price": ctx.bar.close,
                                                  "sma_fast": sma_fast,
                                                  "sma_slow": sma_slow,
                                                  "rsi": current_rsi,
                                                  "entry_hour": current_hour
                                              })
                    elif self.session_impulse_direction == "short":
                        # Confirm short entry: price below SMAs, SMAs crossed bearish, RSI not oversold
                        if ctx.bar.close < sma_fast and sma_fast < sma_slow and current_rsi > self.min_rsi:
                            self.last_entry_bar_index = ctx.bar_index
                            return ctx.signal("short", confidence=0.7,
                                              metadata={
                                                  "reason": "us_session_handoff_short_confirmation",
                                                  "session_impulse": "short",
                                                  "session_close_price": self.session_impulse_price,
                                                  "current_price": ctx.bar.close,
                                                  "sma_fast": sma_fast,
                                                  "sma_slow": sma_slow,
                                                  "rsi": current_rsi,
                                                  "entry_hour": current_hour
                                              })
                # Clear impulse after attempt, whether entered or not, to prevent re-entry on same impulse
                self.session_impulse_direction = None
                self.session_impulse_price = None

        # Exit logic for existing position
        if ctx.has_position():
            # Always exit before the start of the next US session (end of current session logic here is simpler)
            # The strategy aims to hold through the handoff, so exit before the next US session starts
            if current_hour == self.us_session_start_hour - 1: # One hour before next US session
                return ctx.signal("flat", confidence=0.9,
                                  metadata={
                                      "reason": "session_boundary_exit",
                                      "exit_hour": current_hour,
                                      "current_price": ctx.bar.close
                                  })

        self.last_bar_hour = current_hour
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Add logging or adaptive logic here if needed
        pass