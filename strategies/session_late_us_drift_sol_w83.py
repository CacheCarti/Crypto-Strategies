from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class SolSessionBreakout(Strategy):
    METADATA = {
        "name": "SOL Session Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 48, # For 24-bar range and 24-bar median
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 18 # UTC 18:00 (late US session)
        self.session_end_hour = 23 # UTC 23:00 (end of late US session)
        self.cooldown_bars = 6 # Cooldown after a trade
        self.last_signal_bar_index = -self.cooldown_bars - 1
        self.median_range_period = 24 # For median range calculation (24 hours)
        self.breakout_multiplier = 1.0 # How much price needs to move beyond median range
        self.min_move_pct = 0.5 # Minimum percentage move from session open to trigger

        # Store session open and high/low for breakout detection
        self.session_open_price = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_start_bar_index = -1
        self.traded_today = False # To ensure only one trade per day/session logic

    def _get_median_daily_range(self, highs, lows, period):
        if len(highs) < period or len(lows) < period:
            return None
        
        daily_ranges = []
        for i in range(period):
            daily_ranges.append(highs[-(i+1)] - lows[-(i+1)])

        if not daily_ranges:
            return 0.0
        return statistics.median(daily_ranges)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset session state at the start of a new day (UTC 00:00)
        if current_hour == 0 and ctx.bar_index != self.session_start_bar_index:
            self.traded_today = False
            self.session_open_price = None
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_start_bar_index = -1 # Reset for next session start

        # Check for cooldown
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # Start of the session
        if current_hour == self.session_start_hour and ctx.bar_index != self.session_start_bar_index:
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
            self.traded_today = False # Reset for the new session
            return None # Don't trade on the very first bar of the session

        # Update session high/low within the session
        if self.session_open_price is not None and current_hour >= self.session_start_hour and current_hour <= self.session_end_hour:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Exit logic before new day or if session ends and no trade
        if ctx.has_position():
            # Exit if approaching midnight UTC, to prevent holding overnight
            if current_hour == 23 and ctx.position_direction() == "long": # If long, exit before 00:00 UTC
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close_long_exit", "current_hour": current_hour})
            if current_hour == 0 and ctx.position_direction() == "short": # If short, exit after 00:00 UTC (this is the first bar of the new day)
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close_short_exit", "current_hour": current_hour})

            # Check for structure stop if position is active (e.g. price breaches session high/low)
            # This is a basic example; a more complex structure stop could be implemented.
            if ctx.position_direction() == "long" and ctx.bar.close < self.session_low:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "structural_stop_long", "close": ctx.bar.close, "session_low": self.session_low})
            if ctx.position_direction() == "short" and ctx.bar.close > self.session_high:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "structural_stop_short", "close": ctx.bar.close, "session_high": self.session_high})
            return None # Hold position if no exit condition met

        # Entry logic within the session
        if not ctx.has_position() and not self.traded_today and self.session_open_price is not None:
            if current_hour >= self.session_start_hour + 1 and current_hour <= self.session_end_hour: # Trade after 1st bar of session
                median_daily_range = self._get_median_daily_range(highs, lows, self.median_range_period)
                if median_daily_range is None or median_daily_range == 0:
                    return None

                # Calculate the current session's range
                current_session_range = self.session_high - self.session_low

                # Calculate price move from session open
                price_move = ctx.bar.close - self.session_open_price
                price_move_pct = (abs(price_move) / self.session_open_price) * 100

                # Check for significant move relative to median daily range and minimum percentage move
                if current_session_range > median_daily_range * self.breakout_multiplier and price_move_pct >= self.min_move_pct:
                    signal_direction = None
                    reason = ""
                    confidence = 0.6 # Base confidence

                    if price_move > 0: # Bullish breakout
                        signal_direction = "long"
                        reason = "session_bullish_breakout_above_median_range"
                        
                    elif price_move < 0: # Bearish breakout
                        signal_direction = "short"
                        reason = "session_bearish_breakout_below_median_range"
                        
                    if signal_direction:
                        self.traded_today = True
                        self.last_signal_bar_index = ctx.bar_index
                        return ctx.signal(
                            signal_direction,
                            confidence=confidence,
                            metadata={
                                "reason": reason,
                                "session_open_price": self.session_open_price,
                                "current_close": ctx.bar.close,
                                "price_move_pct": price_move_pct,
                                "median_daily_range": median_daily_range,
                                "current_session_range": current_session_range,
                                "breakout_multiplier": self.breakout_multiplier,
                                "current_hour": current_hour
                            }
                        )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Adaptive logic based on position close.
        # For this strategy, we prioritize the "one trade per session" logic.
        pass