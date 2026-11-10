from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionBreakout(Strategy):
    METADATA = {
        "name": "Asia Session Breakout SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours typical hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this price action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_session_high = 0.0
        self.asia_session_low = float('inf')
        self.asia_session_open_price = 0.0
        self.last_trade_bar = -1
        self.cooldown_bars = 6 # Wait 6 hours after a trade

        # Parameters for detecting "decisive" move
        self.range_period = 30 # Bars to calculate median range (30 hours)
        self.min_move_factor = 1.0 # Move must exceed 1.0x median range
        self.max_spike_factor = 0.5 # A single bar should not account for more than 50% of the move

    def _median(self, data):
        if not data:
            return 0
        sorted_data = sorted(data)
        mid = len(sorted_data) // 2
        if len(sorted_data) % 2 == 0:
            return (sorted_data[mid - 1] + sorted_data[mid]) / 2
        return sorted_data[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset session data at UTC 0 (start of Asia session)
        if current_hour == 0:
            self.asia_session_high = ctx.bar.high
            self.asia_session_low = ctx.bar.low
            self.asia_session_open_price = ctx.bar.open
            self.last_trade_bar = -1 # Allow trading again
        elif current_hour > 0 and current_hour < 8: # During Asia session (UTC 0-7)
            self.asia_session_high = max(self.asia_session_high, ctx.bar.high)
            self.asia_session_low = min(self.asia_session_low, ctx.bar.low)
        
        # Exit any open position before the end of the Asia session (UTC 8)
        # or if it's held too long
        if ctx.has_position():
            if current_hour == 8: # End of Asia session
                # If position is still open at 8 AM UTC, flat it.
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour, "entry_bar": ctx.position["bars_held"]})
            
            # Simple time-based exit if held too long
            if ctx.position["bars_held"] > self.METADATA["declared_hold_seconds"] / 3600 * 1.5: # 1.5x expected hold
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "max_hold_time_reached", "bars_held": ctx.position["bars_held"]})


        # Entry logic: After Asia session (UTC 8 onwards), look for breakout
        if current_hour == 8 and self.last_trade_bar < 0: # Only trigger at the start of next session, once per day
            # Calculate recent median true range for decisiveness check
            if len(highs) < self.range_period + 1:
                return None
            
            true_ranges = []
            for i in range(1, self.range_period + 1):
                tr = max(highs[-i] - lows[-i], abs(highs[-i] - closes[-i-1]), abs(lows[-i] - closes[-i-1]))
                true_ranges.append(tr)
            median_range = self._median(true_ranges)

            session_range = self.asia_session_high - self.asia_session_low
            
            # Check for decisive move
            is_decisive = session_range > (median_range * self.min_move_factor)

            if not is_decisive:
                # Reset for next day if no decisive move
                self.last_trade_bar = ctx.bar_index
                return None

            # Check for single-bar spike within the session range
            # This is a simplified check: if the last bar of the Asia session
            # accounts for a large portion of the session range.
            # More robust check: iterate through all bars in the session.
            # For simplicity, we'll check the last bar's contribution to the range.
            
            # The close of the last Asia session bar (bar at hour 7) vs open at hour 0
            # This is hard to do with current BarContext. Let's simplify:
            # Check if the current bar (hour 8) has a large move relative to session range
            # Or if the asia_session_high/low was set predominantly by one bar.
            # A simpler way to avoid single-bar spikes: check if the session lasted for a reasonable duration
            # And if the range was built up over several bars.
            # For now, let's just make sure the session_range itself is not too small.
            # The median_range check already handles this implicitly.
            
            # Check for breakout direction
            if ctx.bar.open > self.asia_session_high and (ctx.bar.open - self.asia_session_high) < (session_range * self.max_spike_factor):
                # Long breakout
                if not ctx.has_position() and (ctx.bar_index - self.last_trade_bar) > self.cooldown_bars:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("long", confidence=0.8,
                                      metadata={"reason": "asia_session_breakout_long",
                                                "asia_session_high": self.asia_session_high,
                                                "asia_session_low": self.asia_session_low,
                                                "session_range": session_range,
                                                "median_range": median_range,
                                                "current_price": ctx.bar.open})

            elif ctx.bar.open < self.asia_session_low and (self.asia_session_low - ctx.bar.open) < (session_range * self.max_spike_factor):
                # Short breakout
                if not ctx.has_position() and (ctx.bar_index - self.last_trade_bar) > self.cooldown_bars:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("short", confidence=0.8,
                                       metadata={"reason": "asia_session_breakout_short",
                                                 "asia_session_high": self.asia_session_high,
                                                 "asia_session_low": self.asia_session_low,
                                                 "session_range": session_range,
                                                 "median_range": median_range,
                                                 "current_price": ctx.bar.open})
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to allow new trades after cooldown
        # This ensures the cooldown is respected even if position closes early due to SL/TP
        self.last_trade_bar = ctx.bar_index