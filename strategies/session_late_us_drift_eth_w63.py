from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionBreakout(Strategy):
    METADATA = {
        "name": "US Session Breakout ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Target 3-hour hold
        "warmup_bars": 50,  # Need enough bars for median range calculation
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 18  # UTC 18:00
        self.entry_hour_end = 23   # UTC 23:00 (inclusive for bar at 23:00)
        self.exit_hour = 0         # Exit before UTC 0:00
        self.range_period = 24     # Lookback for median range
        self.breakout_factor = 1.2 # How much larger than median range the session move should be
        self.max_session_move_pct = 0.03 # Max move before considered exhausted (3%)
        self.min_session_move_pct = 0.005 # Min move to be considered decisive (0.5%)
        self.cooldown_bars = 5     # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # For median range calculation
        self.daily_ranges = []

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        ranges.sort()
        mid = len(ranges) // 2
        return (ranges[mid] + ranges[~mid]) / 2 if len(ranges) % 2 == 0 else ranges[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period * 2) # Need more for range calc
        highs = ctx.highs(self.range_period * 2)
        lows = ctx.lows(self.range_period * 2)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Update daily ranges for median calculation
        if ctx.bar_index > 0 and ctx.bar.timestamp.hour == 0 and ctx.bar.timestamp.day != ctx.closes(2)[0].day:
            # New day, calculate previous day's range
            prev_day_high = max(h for i, h in enumerate(ctx.highs(self.range_period * 2)) if ctx.bar.timestamp.hour == 0 and i < self.range_period * 2 and ctx.bar.timestamp.day != ctx.closes(2)[0].day)
            prev_day_low = min(l for i, l in enumerate(ctx.lows(self.range_period * 2)) if ctx.bar.timestamp.hour == 0 and i < self.range_period * 2 and ctx.bar.timestamp.day != ctx.closes(2)[0].day)
            self.daily_ranges.append(prev_day_high - prev_day_low)
            if len(self.daily_ranges) > self.range_period:
                self.daily_ranges.pop(0)


        # Exit logic
        if ctx.has_position():
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None # Hold position during other hours

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: US session (UTC 18-23)
        if self.entry_hour_start <= current_hour <= self.entry_hour_end:
            # Find the start of the current session (18:00 UTC)
            session_start_bar_index = -1
            for i in range(len(ctx.closes(self.entry_hour_end - self.entry_hour_start + 2)) - 1, -1, -1):
                if ctx.bar.timestamp.hour - i == self.entry_hour_start:
                    session_start_bar_index = len(closes) - 1 - i
                    break
            
            if session_start_bar_index == -1 or session_start_bar_index >= len(closes) - 1:
                return None # Not enough data for current session

            session_open_price = closes[session_start_bar_index]
            session_high = max(highs[session_start_bar_index:])
            session_low = min(lows[session_start_bar_index:])
            current_price = ctx.bar.close

            session_move_long = (current_price - session_open_price) / session_open_price
            session_move_short = (session_open_price - current_price) / session_open_price

            median_daily_range = self._median_range(highs, lows, self.range_period)
            if median_daily_range is None or median_daily_range == 0:
                return None

            median_range_pct = median_daily_range / closes[-1] # Express median range as a percentage of current price

            # Check for decisive move
            is_decisive_long = session_move_long > median_range_pct * self.breakout_factor and session_move_long > self.min_session_move_pct
            is_decisive_short = session_move_short > median_range_pct * self.breakout_factor and session_move_short > self.min_session_move_pct

            # Check for exhaustion
            is_exhausted_long = session_move_long > self.max_session_move_pct
            is_exhausted_short = session_move_short > self.max_session_move_pct
            
            # Skip single-bar spikes: check if the move was mostly in the current bar
            # Compare current bar's move to total session move
            current_bar_move_long = (current_price - ctx.bar.open) / ctx.bar.open
            current_bar_move_short = (ctx.bar.open - current_price) / ctx.bar.open

            spike_threshold = 0.7 # If >70% of session move is in current bar, it's a spike
            is_spike_long = session_move_long > 0 and current_bar_move_long / session_move_long > spike_threshold
            is_spike_short = session_move_short > 0 and current_bar_move_short / session_move_short > spike_threshold

            if is_decisive_long and not is_exhausted_long and not is_spike_long:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "us_session_long_breakout",
                        "session_open_price": session_open_price,
                        "current_price": current_price,
                        "session_move_pct": session_move_long * 100,
                        "median_daily_range_pct": median_range_pct * 100,
                        "breakout_factor_applied": self.breakout_factor
                    }
                )
            elif is_decisive_short and not is_exhausted_short and not is_spike_short:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "us_session_short_breakout",
                        "session_open_price": session_open_price,
                        "current_price": current_price,
                        "session_move_pct": session_move_short * 100,
                        "median_daily_range_pct": median_range_pct * 100,
                        "breakout_factor_applied": self.breakout_factor
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: adaptive sizing or logging
        pass