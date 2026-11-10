from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightSessionBreakout(Strategy):
    METADATA = {
        "name": "Overnight Session Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-hour avg hold
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20 # For median range
        self.min_move_ratio = 0.8 # Session move must exceed this ratio of median range
        self.max_move_ratio = 2.0 # Session move should not be exhausted (e.g., not > 2x median range)
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

    def _median(self, values):
        if not values:
            return 0
        sorted_values = sorted(values)
        mid = len(sorted_values) // 2
        if len(sorted_values) % 2 == 0:
            return (sorted_values[mid - 1] + sorted_values[mid]) / 2
        else:
            return sorted_values[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Calculate recent median true range
        true_ranges = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            true_ranges.append(tr)
        
        median_true_range = self._median(true_ranges[-self.lookback_period:])
        if median_true_range == 0:
            return None

        # Overnight session: 0 UTC to 6 UTC (inclusive of 0, exclusive of 6)
        # Entry logic fires at 06:00 UTC (bar for 05:00-06:00 closes)
        if current_hour == 6 and not ctx.has_position():
            if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
                return None

            # Calculate the overnight session's range and direction
            # We need bars from 00:00 to 05:00 (6 bars)
            # Find the min/max price during this period
            session_low = math.inf
            session_high = -math.inf
            session_open = None
            session_close = ctx.closes(1)[0] # Close of the 05:00-06:00 bar

            # Iterate through the last 6 bars (00:00 to 05:00 UTC)
            # ctx.lows(n) gives oldest first, so we need to grab the relevant slice
            if len(lows) < 6 or len(highs) < 6 or len(closes) < 6:
                return None

            # The current bar is the 05:00-06:00 bar. We need the 6 bars before it.
            # ctx.closes(n) gives last n bars, oldest first. So `closes[-6]` would be the 00:00 bar.
            # We want the range of the bars from 00:00 to 05:00.
            # Let's get the OHLC for the actual overnight period (00:00 to 05:00 UTC).
            # The current bar is 06:00. We need to look back 6 bars to get the 00:00 bar.
            
            # The bar from 00:00-01:00 is `closes[-6]`, `highs[-6]`, `lows[-6]`
            # The bar from 05:00-06:00 is `closes[-1]`, `highs[-1]`, `lows[-1]`
            # So, the range of interest is from `closes[-6]` up to `closes[-1]` (inclusive)

            # To get the price at 00:00 UTC, we use the open of the bar that starts at 00:00.
            # This is the open of `ctx.opens(6)[0]` if we are currently at 05:00 UTC bar close.
            # Or, if current_hour is 6 (meaning the bar at 05:00 UTC is closing):
            # The bars for 00:00, 01:00, ..., 05:00 are available in the history.
            # ctx.closes(n) returns n values, oldest first.
            # So, to get the last 6 bars (00:00 to 05:00), we need ctx.closes(6+1).
            # The bar at 00:00 is closes[-7], opens[-7], highs[-7], lows[-7]
            # The bar at 05:00 is closes[-2], opens[-2], highs[-2], lows[-2]

            # Let's re-evaluate getting the 00:00-06:00 (exclusive) session range.
            # We are at the close of the 05:00-06:00 bar.
            # We need the range from the open of the 00:00-01:00 bar to the close of the 05:00-06:00 bar.
            # The relevant bars are `opens[-6]` through `closes[-1]`.
            
            # This means we need at least 6 bars *before* the current one to represent the session.
            if len(closes) < 6: # Need at least 6 bars to define the overnight session from 00:00 to 05:00
                return None

            session_open = ctx.opens(6)[0] # Open of the 00:00-01:00 bar
            session_close_price = ctx.closes(1)[0] # Close of the 05:00-06:00 bar

            # Find high/low of the overnight session (00:00 to 05:00 UTC, inclusive)
            # The current bar is 05:00-06:00. So we need the 6 bars before it.
            # highs[-6]...highs[-1] and lows[-6]...lows[-1]
            # These are the highs/lows of the bars that STARTED at 00:00, 01:00, ..., 05:00.
            
            # The correct indices for the 6 bars (00:00 to 05:00) are from `[-6]` to `[-1]`
            session_high = max(highs[-6:])
            session_low = min(lows[-6:])

            session_range = session_high - session_low
            session_move = session_close_price - session_open

            # Check for decisive but not exhausted move
            if session_range > 0:
                move_ratio_to_range = abs(session_move) / median_true_range
                
                if self.min_move_ratio < move_ratio_to_range < self.max_move_ratio:
                    confidence = min(1.0, move_ratio_to_range / self.max_move_ratio) # Scale confidence with move strength
                    
                    if session_move > 0: # Bullish breakout
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            metadata={
                                "reason": "overnight_bullish_breakout",
                                "session_open": session_open,
                                "session_close": session_close_price,
                                "session_move_pct": (session_move / session_open) * 100,
                                "session_range": session_range,
                                "median_true_range": median_true_range,
                                "move_to_median_ratio": move_ratio_to_range
                            }
                        )
                    elif session_move < 0: # Bearish breakout
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=confidence,
                            metadata={
                                "reason": "overnight_bearish_breakout",
                                "session_open": session_open,
                                "session_close": session_close_price,
                                "session_move_pct": (session_move / session_open) * 100,
                                "session_range": session_range,
                                "median_true_range": median_true_range,
                                "move_to_median_ratio": move_ratio_to_range
                            }
                        )

        # Exit logic: If a position is open, consider exiting before the next US session (e.g., 14:00 UTC)
        # or if the current hour is late in the day (e.g., 22:00 UTC) to avoid holding overnight again.
        if ctx.has_position():
            # Exit before the 14:00 UTC bar closes (i.e., at 13:00 UTC bar close)
            # or if it's getting late (e.g. 22:00 UTC bar close)
            if current_hour == 13 or current_hour == 22:
                self.last_trade_bar = ctx.bar_index # Reset cooldown
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "session_close_time_exit",
                        "current_hour": current_hour,
                        "entry_price": ctx.position["entry_price"]
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just reset cooldown if needed (already done on signal)
        pass