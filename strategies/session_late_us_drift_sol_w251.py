from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class SolSessionBreakout(Strategy):
    METADATA = {
        "name": "SOL Session Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours hold time
        "warmup_bars": 48, # Need 24 bars for session range, plus some for median range
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 18 # UTC 18:00 (late US session)
        self.session_end_hour = 24   # UTC 00:00 (end of session, or start of next day)
        self.lookback_period = 24    # 24 hours for median range calculation
        self.min_move_multiplier = 1.0 # Multiplier for median range to define "decisive" move
        self.max_exhaustion_factor = 0.75 # Max percentage of session range already moved
        self.cooldown_bars = 4       # Cooldown period after a trade (4 hours)
        self.last_signal_bar = -self.cooldown_bars

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        return statistics.median(ranges)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1) # Need one more for range calculation
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for cooldown
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Logic to exit existing position before session end (UTC 00:00)
        if ctx.has_position() and current_hour == 0:
            if ctx.position_direction() == "long":
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_close_exit", "current_hour": current_hour, "price": ctx.bar.close})
            elif ctx.position_direction() == "short":
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_close_exit", "current_hour": current_hour, "price": ctx.bar.close})

        # Entry logic within the late US session
        if not ctx.has_position() and self.session_start_hour <= current_hour < self.session_end_hour:
            # Calculate median true range for decisiveness
            median_daily_range = self._median_range(highs[:-1], lows[:-1], self.lookback_period) # Exclude current bar
            if median_daily_range is None:
                return None

            # Look for a decisive move within the current session
            # We need to find the start of the session within the recent bars
            session_start_bar_index = -1
            for i in range(len(highs) - 1, -1, -1):
                bar_time = ctx.bar.timestamp - (ctx.bar_index - i) * (ctx.bar.timestamp - (ctx.bar.timestamp.replace(minute=0, second=0, microsecond=0) - ctx.bar.timestamp.replace(minute=0, second=0, microsecond=0)).total_seconds() // 3600) # Approximate bar time
                if bar_time.hour == self.session_start_hour:
                    session_start_bar_index = i
                    break

            if session_start_bar_index == -1 or session_start_bar_index >= len(closes) - 1:
                return None # Session start not in history or current bar is the start

            session_high = max(highs[session_start_bar_index:])
            session_low = min(lows[session_start_bar_index:])
            session_open = opens[session_start_bar_index] # Assuming opens is available
            session_current_price = ctx.bar.close

            session_range = session_high - session_low

            # Check for decisive move
            min_decisive_move = median_daily_range * self.min_move_multiplier

            # Check for exhaustion
            current_move_from_session_open = session_current_price - session_open
            exhaustion_threshold_long = session_range * self.max_exhaustion_factor
            exhaustion_threshold_short = -session_range * self.max_exhaustion_factor

            # Long condition: Price broke above session high and move is decisive but not exhausted
            if ctx.bar.close > session_high and current_move_from_session_open > min_decisive_move:
                if (current_move_from_session_open / session_range) < self.max_exhaustion_factor:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("long", confidence=0.7,
                                      metadata={"reason": "session_breakout_long",
                                                "current_hour": current_hour,
                                                "session_open": session_open,
                                                "session_high": session_high,
                                                "current_price": ctx.bar.close,
                                                "median_range": median_daily_range,
                                                "move_from_open": current_move_from_session_open,
                                                "session_range": session_range})

            # Short condition: Price broke below session low and move is decisive but not exhausted
            if ctx.bar.close < session_low and current_move_from_session_open < -min_decisive_move:
                if (current_move_from_session_open / session_range) > exhaustion_threshold_short: # Check for exhaustion in short direction
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("short", confidence=0.7,
                                      metadata={"reason": "session_breakout_short",
                                                "current_hour": current_hour,
                                                "session_open": session_open,
                                                "session_low": session_low,
                                                "current_price": ctx.bar.close,
                                                "median_range": median_daily_range,
                                                "move_from_open": current_move_from_session_open,
                                                "session_range": session_range})
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt parameters based on performance
        pass