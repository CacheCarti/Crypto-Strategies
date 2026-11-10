from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionBreakout(Strategy):
    METADATA = {
        "name": "Asia Session Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hour hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_session_high = -1.0
        self.asia_session_low = float('inf')
        self.asia_session_start_bar_index = -1
        self.last_signal_bar_index = -1
        self.cooldown_bars = 6 # Wait 6 hours after a signal
        self.range_period = 24 # For median range calculation

    def _median(self, lst):
        sorted_lst = sorted(lst)
        n = len(sorted_lst)
        if n % 2 == 0:
            return (sorted_lst[n // 2 - 1] + sorted_lst[n // 2]) / 2
        else:
            return sorted_lst[n // 2]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # --- ASIA SESSION LOGIC ---
        # Asia session: UTC 0-8 (9 bars, including 0 and 8)
        # We collect data from 00:00 UTC to 08:00 UTC (inclusive of 08:00 bar)
        if current_hour >= 0 and current_hour <= 8:
            if self.asia_session_start_bar_index == -1: # First bar of a new Asia session
                self.asia_session_start_bar_index = ctx.bar_index
                self.asia_session_high = ctx.bar.high
                self.asia_session_low = ctx.bar.low
            else:
                self.asia_session_high = max(self.asia_session_high, ctx.bar.high)
                self.asia_session_low = min(self.asia_session_low, ctx.bar.low)
            return None # Don't trade during Asia session, just collect data

        # --- Breakout evaluation at the start of the next session (after 08:00 UTC) ---
        if current_hour == 9 and self.asia_session_start_bar_index != -1:
            # Calculate Asia session range
            session_range = self.asia_session_high - self.asia_session_low

            # Calculate recent median daily range
            if len(highs) < self.range_period or len(lows) < self.range_period:
                return None

            daily_ranges = []
            for i in range(self.range_period):
                # Simple approximation for daily range from hourly data
                # This assumes 'day' is roughly the last 24 bars
                daily_ranges.append(highs[-i-1] - lows[-i-1])

            median_range = self._median(daily_ranges)

            # Check for decisive move (1.2x median range) but not exhausted (max 2x median range)
            # and not a single bar spike (session range > 0, not just one high/low)
            if session_range > 0.001 and median_range > 0: # Avoid division by zero and tiny ranges
                range_ratio = session_range / median_range

                if ctx.bar.close > self.asia_session_high and range_ratio > 0.8 and range_ratio < 2.0:
                    # Long breakout
                    if not ctx.has_position():
                        self.last_signal_bar_index = ctx.bar_index
                        self.asia_session_start_bar_index = -1 # Reset for next day
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "asia_session_breakout_long",
                                "asia_high": self.asia_session_high,
                                "asia_low": self.asia_session_low,
                                "session_range": session_range,
                                "median_range": median_range,
                                "range_ratio": range_ratio,
                                "close": ctx.bar.close
                            }
                        )
                elif ctx.bar.close < self.asia_session_low and range_ratio > 0.8 and range_ratio < 2.0:
                    # Short breakout
                    if not ctx.has_position():
                        self.last_signal_bar_index = ctx.bar_index
                        self.asia_session_start_bar_index = -1 # Reset for next day
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            metadata={
                                "reason": "asia_session_breakout_short",
                                "asia_high": self.asia_session_high,
                                "asia_low": self.asia_session_low,
                                "session_range": session_range,
                                "median_range": median_range,
                                "range_ratio": range_ratio,
                                "close": ctx.bar.close
                            }
                        )
            self.asia_session_start_bar_index = -1 # Reset even if no signal, to avoid re-evaluating

        # --- Exit logic for existing positions ---
        # Exit before the next Asia session starts (00:00 UTC) or if it's the last hour of the day (23:00 UTC)
        if ctx.has_position() and current_hour >= 22: # Give it some time to work, but flat before next Asia session
            if ctx.position_direction() == "long" and ctx.bar.close < self.asia_session_low: # Structure stop
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "structure_stop_long", "exit_hour": current_hour, "session_low": self.asia_session_low})
            elif ctx.position_direction() == "short" and ctx.bar.close > self.asia_session_high: # Structure stop
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "structure_stop_short", "exit_hour": current_hour, "session_high": self.asia_session_high})
            elif current_hour == 23: # Time-based exit before new Asia session
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "time_based_exit_eod", "exit_hour": current_hour})


        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details
        pass