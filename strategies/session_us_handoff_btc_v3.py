from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionUSImpulseBTC(Strategy):
    METADATA = {
        "name": "Session US Impulse BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Up to 3 hours, covering session handoff
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt"], # Using funding rate as a sentiment/regime filter
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13  # UTC 13:00
        self.us_session_end_hour = 21    # UTC 21:00 (exit before this)
        self.min_session_duration_bars = 5 # Minimum bars to define an impulse
        self.max_hold_bars = 3 # Max 3 bars to carry over to next session
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars

        self.ema_period_fast = 10
        self.ema_period_slow = 20
        self.atr_period = 14

        self.session_entry_price = 0.0
        self.session_direction = None
        self.session_bar_index = -1

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + max(self.ema_period_slow, self.atr_period))
        highs = ctx.highs(self.warmup_bars + max(self.ema_period_slow, self.atr_period))
        lows = ctx.lows(self.warmup_bars + max(self.ema_period_slow, self.atr_period))

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Calculate indicators
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)
        current_atr = self._atr(highs, lows, closes, self.atr_period)

        if ema_fast is None or ema_slow is None or current_atr is None:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # --- Position Management ---
        if ctx.has_position():
            # Exit if session end hour is reached, or max hold bars exceeded
            if current_hour >= self.us_session_end_hour:
                 # Ensure we are not exiting too early on the same bar we entered
                if ctx.bar_index > self.session_bar_index:
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "session_end_time",
                        "current_hour": current_hour,
                        "entry_bar_index": self.session_bar_index,
                        "bars_held": ctx.bar_index - self.session_bar_index,
                        "session_direction": self.session_direction
                    })

            if ctx.bar_index - self.session_bar_index >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "max_hold_bars_exceeded",
                    "bars_held": ctx.bar_index - self.session_bar_index,
                    "max_hold_bars": self.max_hold_bars,
                    "session_direction": self.session_direction
                })

            # Check for a "structure stop" - reversal against the session impulse
            if self.session_direction == "long" and ctx.bar.close < ema_fast:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "structure_stop_long",
                    "entry_price": self.session_entry_price,
                    "current_price": ctx.bar.close,
                    "ema_fast": ema_fast,
                    "session_direction": self.session_direction
                })
            elif self.session_direction == "short" and ctx.bar.close > ema_fast:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "structure_stop_short",
                    "entry_price": self.session_entry_price,
                    "current_price": ctx.bar.close,
                    "ema_fast": ema_fast,
                    "session_direction": self.session_direction
                })
            return None # Hold position

        # --- Entry Logic ---
        # Only consider entering during the US session
        if current_hour < self.us_session_start_hour or current_hour >= self.us_session_end_hour:
            return None

        # Ensure enough bars have passed since the start of the US session to identify an impulse
        # We need to find the start of the current US session
        session_open_bar_index = ctx.bar_index
        for i in range(ctx.bar_index, -1, -1):
            if ctx.bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0).hour == self.us_session_start_hour:
                session_open_bar_index = i
                break
        
        # This logic is flawed for finding session start. Simpler: only trade if previous bar was NOT in session, and current bar IS.
        # This prevents multiple entries within the same session.
        if ctx.bar_index > 0 and ctx.bar.timestamp.hour == self.us_session_start_hour and ctx.closes(2)[0].hour != self.us_session_start_hour:
            # This is the first bar of the US session, or a new US session after a break
            self.session_entry_price = 0.0 # Reset for new session
            self.session_direction = None
            self.session_bar_index = -1

        # Calculate impulse after min_session_duration_bars
        if ctx.bar.timestamp.hour >= self.us_session_start_hour + self.min_session_duration_bars:
            # Get the price at the start of the US session
            # This is simplified: assuming the current bar is part of the impulse measurement.
            # A more robust approach would track the actual session start bar.
            # For this simple example, we'll use a recent bar as a proxy for 'session start' for impulse.
            # Let's say, the close 'min_session_duration_bars' ago.
            
            # Look back to the start of the impulse measurement
            impulse_start_closes = ctx.closes(self.min_session_duration_bars + 1)
            impulse_start_price = impulse_start_closes[0] # Close from N bars ago

            price_change = ctx.bar.close - impulse_start_price
            
            # Use ATR to normalize the impulse
            impulse_magnitude = abs(price_change) / current_atr

            # Funding rate as a filter: positive funding for long, negative for short
            funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
            
            # Long entry conditions
            if price_change > 0 and impulse_magnitude > 0.5 and ctx.bar.close > ema_slow and funding_rate > 0.00005:
                self.session_entry_price = ctx.bar.close
                self.session_direction = "long"
                self.session_bar_index = ctx.bar_index
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("long", confidence=0.75, metadata={
                    "reason": "us_session_long_impulse",
                    "impulse_magnitude": impulse_magnitude,
                    "price_change": price_change,
                    "current_atr": current_atr,
                    "current_price": ctx.bar.close,
                    "ema_slow": ema_slow,
                    "funding_rate": funding_rate
                })

            # Short entry conditions
            elif price_change < 0 and impulse_magnitude > 0.5 and ctx.bar.close < ema_slow and funding_rate < -0.00005:
                self.session_entry_price = ctx.bar.close
                self.session_direction = "short"
                self.session_bar_index = ctx.bar_index
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("short", confidence=0.75, metadata={
                    "reason": "us_session_short_impulse",
                    "impulse_magnitude": impulse_magnitude,
                    "price_change": price_change,
                    "current_atr": current_atr,
                    "current_price": ctx.bar.close,
                    "ema_slow": ema_slow,
                    "funding_rate": funding_rate
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session specific variables after a position closes
        self.session_entry_price = 0.0
        self.session_direction = None
        self.session_bar_index = -1
        # Cooldown is handled by last_trade_bar_index in on_bar