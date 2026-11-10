from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class AsiaSessionBreakout(Strategy):
    METADATA = {
        "name": "Asia Session Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours
        "warmup_bars": 40,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_session_range_period = 24  # Bars for median range (24 hours)
        self.min_range_factor = 1.5  # Min range for decisive move
        self.max_range_factor = 3.0  # Max range to avoid exhausted moves
        self.cooldown_bars = 6
        self.last_trade_bar = -self.cooldown_bars
        self.last_asia_session_start_bar = -1
        self.entry_bar_idx = -1

        # Store daily ranges to calculate median
        self.daily_ranges = collections.deque(maxlen=self.asia_session_range_period)
        self.current_asia_high = -1.0
        self.current_asia_low = float('inf')
        self.asia_session_open_price = 0.0

    def _median(self, data):
        if not data:
            return 0
        sorted_data = sorted(data)
        n = len(sorted_data)
        if n % 2 == 1:
            return sorted_data[n // 2]
        else:
            return (sorted_data[n // 2 - 1] + sorted_data[n // 2]) / 2

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check if we are starting a new Asia session (UTC 0-8)
        # Asia session starts at UTC 0, ends at UTC 8
        is_asia_session_start = (current_hour == 0)

        # Update daily range for median calculation
        if current_hour == 23: # End of UTC day, record the day's range
            daily_high = max(highs[-24:]) # Assuming 1h bars, last 24 bars form a day
            daily_low = min(lows[-24:])
            self.daily_ranges.append(daily_high - daily_low)

        # --- Position Management ---
        if ctx.has_position():
            # Exit before the end of the Asia session (UTC 8) if still in a position
            # This is specifically for positions opened based on Asia session breakout
            # We want to exit before the next major session (Europe/London) starts.
            if ctx.bar.timestamp.hour == 8 and ctx.position_direction() == "long":
                 # Ensure we don't exit if the position was opened after 8 AM (which shouldn't happen with this strategy)
                if self.entry_bar_idx != -1 and ctx.bar_index - self.entry_bar_idx < 8: # Arbitrary check to avoid exiting positions opened too recently
                     return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "exit_before_europe_session_start",
                        "current_price": ctx.bar.close,
                        "session_end_hour": 8
                    })
            if ctx.bar.timestamp.hour == 8 and ctx.position_direction() == "short":
                 if self.entry_bar_idx != -1 and ctx.bar_index - self.entry_bar_idx < 8:
                     return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "exit_before_europe_session_start",
                        "current_price": ctx.bar.close,
                        "session_end_hour": 8
                    })

            # If we are in a position, just hold. No new signals until position closes.
            return None

        # --- Session Tracking ---
        if is_asia_session_start:
            self.current_asia_high = highs[-1]
            self.current_asia_low = lows[-1]
            self.asia_session_open_price = closes[-1]
            self.last_asia_session_start_bar = ctx.bar_index
        elif current_hour > 0 and current_hour < 8 and ctx.bar_index > self.last_asia_session_start_bar:
            # Update high/low during Asia session (UTC 0-7)
            self.current_asia_high = max(self.current_asia_high, highs[-1])
            self.current_asia_low = min(self.current_asia_low, lows[-1])

        # --- Entry Logic (after Asia session ends, into Europe/London session) ---
        # Only consider entries after the Asia session (UTC 0-8) has completed
        # and we are outside the Asia session hours (i.e., current_hour >= 8)
        if current_hour >= 8 and (ctx.bar_index - self.last_trade_bar) > self.cooldown_bars:
            if ctx.bar_index <= self.last_asia_session_start_bar + 8: # Ensure Asia session has completed (8 bars after start)
                # Ensure we have enough daily ranges to calculate median
                if len(self.daily_ranges) < self.asia_session_range_period // 2:
                    return None

                median_daily_range = self._median(self.daily_ranges)
                asia_session_range = self.current_asia_high - self.current_asia_low

                # Check for decisive but not exhausted move
                if asia_session_range > median_daily_range * self.min_range_factor and \
                   asia_session_range < median_daily_range * self.max_range_factor:

                    # Breakout above Asia session high
                    if ctx.bar.close > self.current_asia_high:
                        # Avoid single-bar spikes: ensure the close is sustained above
                        # or that the prior close was also strong.
                        # Simple check: current close is significantly above the high, and not just a wick.
                        if ctx.bar.close > self.current_asia_high + (asia_session_range * 0.1): # 10% of Asia range above high
                            self.last_trade_bar = ctx.bar_index
                            self.entry_bar_idx = ctx.bar_index
                            return ctx.signal("long", confidence=0.8, metadata={
                                "reason": "asia_session_breakout_long",
                                "asia_high": self.current_asia_high,
                                "asia_low": self.current_asia_low,
                                "asia_range": asia_session_range,
                                "median_daily_range": median_daily_range,
                                "current_close": ctx.bar.close
                            })

                    # Breakout below Asia session low
                    elif ctx.bar.close < self.current_asia_low:
                        # Avoid single-bar spikes
                        if ctx.bar.close < self.current_asia_low - (asia_session_range * 0.1): # 10% of Asia range below low
                            self.last_trade_bar = ctx.bar_index
                            self.entry_bar_idx = ctx.bar_index
                            return ctx.signal("short", confidence=0.8, metadata={
                                "reason": "asia_session_breakout_short",
                                "asia_high": self.current_asia_high,
                                "asia_low": self.current_asia_low,
                                "asia_range": asia_session_range,
                                "median_daily_range": median_daily_range,
                                "current_close": ctx.bar.close
                            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset entry bar index upon position close
        self.entry_bar_idx = -1
        # Log the outcome or adapt sizing if needed
        # print(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps, held for {position['bars_held']} bars")