from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanSessionImpulse(Strategy):
    METADATA = {
        "name": "European Session Impulse",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold for one session, 1hr bars
        "warmup_bars": 24, # Need enough bars for 24-period VWAP
        "required_features": [], # No specific features required for this structure
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7 AM
        self.entry_hour_end = 10   # UTC 10 AM (allow entry up to this hour)
        self.exit_hour = 12        # UTC 12 PM (exit by this hour)
        self.session_lookback = 4  # Bars for session impulse (4 hours)
        self.min_impulse_std_dev = 0.5 # Minimum impulse strength in terms of std dev
        self.cooldown_bars = 4     # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.session_lookback + 1)
        if len(closes) < self.session_lookback + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic for open positions
        if ctx.has_position():
            # If it's past the exit hour, or if the current bar is the exit hour
            if current_hour >= self.exit_hour:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_exit_time",
                    "current_hour": current_hour
                })
            # No other exit conditions for this strategy, relies on TP/SL or time-based exit
            return None

        # Entry logic
        if self.entry_hour_start <= current_hour < self.entry_hour_end:
            # Check if we already tried to enter today (simple daily entry limit)
            # This is a bit simplified, ideally check for a specific date
            if self.last_trade_bar > ctx.bar_index - 24: # Roughly one trade per day
                return None

            # Calculate session impulse
            # We look at the price change over the last `session_lookback` hours
            session_prices = closes[-(self.session_lookback + 1):]
            if len(session_prices) < self.session_lookback + 1:
                return None

            price_change = session_prices[-1] - session_prices[0]
            
            # Calculate volatility for normalization
            recent_closes = ctx.closes(self.session_lookback * 2) # Use a bit longer period for vol
            if len(recent_closes) < self.session_lookback * 2:
                return None
            
            std_dev = self._stddev(recent_closes, self.session_lookback * 2)

            if std_dev is None or std_dev == 0:
                return None

            # Normalize price change by volatility to get a "z-score" like impulse
            impulse_strength = price_change / std_dev

            # Check for a significant impulse
            if impulse_strength > self.min_impulse_std_dev:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.7, metadata={
                    "reason": "european_session_long_impulse",
                    "entry_hour": current_hour,
                    "price_change": price_change,
                    "impulse_strength": impulse_strength,
                    "std_dev": std_dev
                })
            elif impulse_strength < -self.min_impulse_std_dev:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.7, metadata={
                    "reason": "european_session_short_impulse",
                    "entry_hour": current_hour,
                    "price_change": price_change,
                    "impulse_strength": impulse_strength,
                    "std_dev": std_dev
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this simple strategy.
        # The cooldown already handles preventing immediate re-entry.
        pass