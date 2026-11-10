from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightSessionBreakout(Strategy):
    METADATA = {
        "name": "OvernightSessionBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-hour avg hold
        "warmup_bars": 48, # For 24-period range and 12-period volume
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_start_hour = 0
        self.overnight_end_hour = 6 # 00:00 to 05:59 UTC
        self.session_break_hour = 6 # After 06:00 UTC, look for breakout
        self.min_session_range_period = 24 # for median range calculation
        self.min_breakout_range_multiplier = 1.0 # breakout must exceed median range * multiplier
        self.max_session_exhaustion_factor = 0.6 # don't trade if session move already too big
        self.min_volume_ratio = 1.5 # breakout bar volume must be higher than avg
        self.volume_avg_period = 12
        self.cooldown_bars = 4
        self.last_trade_bar = -self.cooldown_bars - 1 # Ensure trade on first eligible bar

        self.last_session_close = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_start_bar_index = -1
        self.session_direction = None # "up", "down", or None
        self.session_move_abs = 0.0

    def _median(self, values):
        if not values:
            return 0.0
        sorted_values = sorted(values)
        mid = len(sorted_values) // 2
        if len(sorted_values) % 2 == 0:
            return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0
        else:
            return sorted_values[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.min_session_range_period * 2) # Need more for range history
        highs = ctx.highs(self.min_session_range_period * 2)
        lows = ctx.lows(self.min_session_range_period * 2)
        volumes = ctx.volumes(self.volume_avg_period)

        if len(closes) < self.METADATA["warmup_bars"] or len(volumes) < self.volume_avg_period:
            return None

        current_hour = ctx.bar.timestamp.hour

        # -- Session Tracking --
        # Reset session at the start of the overnight window (00:00 UTC)
        if current_hour == self.overnight_start_hour and ctx.bar_index != self.session_start_bar_index:
            self.last_session_close = closes[-1] # Previous day's close
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
            self.session_direction = None
            self.session_move_abs = 0.0
            # ctx.logger.info(f"New session started at bar {ctx.bar_index}, hour {current_hour}")

        # Update session high/low during the overnight window
        if self.overnight_start_hour <= current_hour < self.overnight_end_hour:
            if ctx.bar.high > self.session_high:
                self.session_high = ctx.bar.high
            if ctx.bar.low < self.session_low:
                self.session_low = ctx.bar.low

        # Calculate session direction and move at the end of the overnight window
        if current_hour == (self.overnight_end_hour - 1) and ctx.bar_index == self.session_start_bar_index + (self.overnight_end_hour - self.overnight_start_hour - 1):
            if self.last_session_close is not None:
                if ctx.bar.close > self.last_session_close:
                    self.session_direction = "up"
                    self.session_move_abs = ctx.bar.close - self.last_session_close
                elif ctx.bar.close < self.last_session_close:
                    self.session_direction = "down"
                    self.session_move_abs = self.last_session_close - ctx.bar.close
                # ctx.logger.info(f"Session {ctx.bar_index} closed. Direction: {self.session_direction}, Move: {self.session_move_abs:.2f}")

        # -- Signal Generation --
        if ctx.has_position():
            # Exit before the next overnight session starts (e.g., US session close)
            # Or if the session structure breaks (e.g., price re-enters overnight range)
            # For simplicity, we exit before the next overnight session.
            # This is a basic example; more complex structure stops could be added.
            if current_hour == self.overnight_start_hour - 1: # One hour before new session
                 return ctx.signal("flat", confidence=0.6, metadata={"reason": "pre_session_exit", "hour": current_hour})
            return None # Hold position

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar <= self.cooldown_bars:
            return None

        # Only look for breakouts after the overnight session ends
        if current_hour == self.session_break_hour:
            # Ensure we have valid session data
            if self.session_direction is None or self.last_session_close is None:
                return None

            # Calculate recent median daily range
            recent_ranges = []
            for i in range(len(closes) - self.min_session_range_period, len(closes) -1):
                if i < 0: continue # ensure valid index
                recent_ranges.append(highs[i] - lows[i])
            median_range = self._median(recent_ranges)
            min_breakout_move = median_range * self.min_breakout_range_multiplier

            # Check for "exhaustion" - if the session move was already very large
            if self.session_move_abs > median_range * self.max_session_exhaustion_factor:
                # ctx.logger.info(f"Skipping trade due to session exhaustion. Move: {self.session_move_abs:.2f}, Median Range: {median_range:.2f}")
                return None

            # Check for "single-bar spikes" - ensure the breakout is sustained not just a wick
            # This is implicitly handled by checking current close against session boundary and median range
            # A more explicit check would be to see if the current bar's range is excessively large compared to its body

            # Volume confirmation
            avg_volume = sum(volumes) / len(volumes)
            if ctx.bar.volume < avg_volume * self.min_volume_ratio:
                return None

            # Long breakout
            if self.session_direction == "up" and ctx.bar.close > self.session_high:
                if (ctx.bar.close - self.session_high) > min_breakout_move:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "overnight_session_long_breakout",
                            "session_high": self.session_high,
                            "breakout_move": ctx.bar.close - self.session_high,
                            "median_range": median_range,
                            "volume_ratio": ctx.bar.volume / avg_volume,
                            "session_move_abs": self.session_move_abs
                        }
                    )

            # Short breakout
            elif self.session_direction == "down" and ctx.bar.close < self.session_low:
                if (self.session_low - ctx.bar.close) > min_breakout_move:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "overnight_session_short_breakout",
                            "session_low": self.session_low,
                            "breakout_move": self.session_low - ctx.bar.close,
                            "median_range": median_range,
                            "volume_ratio": ctx.bar.volume / avg_volume,
                            "session_move_abs": self.session_move_abs
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Add adaptive logic here based on position outcome
        # e.g., if position["return_bps"] > 0: self.win_count += 1
        pass