from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class USSessionBreakoutFollower(Strategy):
    METADATA = {
        "name": "US Session Breakout Follower",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for 1-2 sessions
        "warmup_bars": 50,              # Need enough data for ATR and median range
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 13  # UTC 13:00 (US session open)
        self.session_end_hour = 21    # UTC 21:00 (US session close for entry/exit)
        self.lookback_period_atr = 14 # For ATR
        self.lookback_period_median_range = 20 # For median range comparison
        self.min_session_move_factor = 1.0 # Session move must exceed this factor of median range
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars # Initialize to allow immediate trade

        self.session_high: Optional[float] = None
        self.session_low: Optional[float] = None
        self.session_open: Optional[float] = None
        self.session_start_bar_index: Optional[int] = None
        self.last_session_close_price: Optional[float] = None
        self.trade_made_this_session = False

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = sorted([h - l for h, l in zip(highs[-period:], lows[-period:])])
        mid = len(ranges) // 2
        return (ranges[mid] + ranges[~mid]) / 2 if len(ranges) % 2 == 0 else ranges[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period_atr + 1)
        highs = ctx.highs(self.lookback_period_atr + 1)
        lows = ctx.lows(self.lookback_period_atr + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset session data at the start of a new session (or if it's the first bar)
        if current_hour == self.session_start_hour and (self.session_start_bar_index is None or ctx.bar_index > self.session_start_bar_index):
            self.session_open = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
            self.trade_made_this_session = False
            # If a position is open, flat it before the new session begins, if it's past the old session's end hour
            if ctx.has_position() and current_hour >= self.session_end_hour:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        # Update session high/low if within the session
        if self.session_open is not None and current_hour >= self.session_start_hour:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Exit logic for existing positions
        if ctx.has_position():
            # Mandatory exit before session_end_hour to prevent holding into next session's open unless intended
            if current_hour >= self.session_end_hour and ctx.position_direction() is not None:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_close_exit", "current_hour": current_hour, "entry_price": ctx.position["entry_price"]})
            return None # Hold position during session

        # Entry logic: only if no position and cooldown passed and within session hours
        if not ctx.has_position() and (ctx.bar_index - self.last_trade_bar_index) >= self.cooldown_bars and not self.trade_made_this_session:
            # Check if we are past the session start hour but not yet at the session end hour
            if current_hour >= self.session_start_hour and current_hour < self.session_end_hour:
                # Need at least a few bars into the session to establish a move
                if self.session_start_bar_index is not None and ctx.bar_index > self.session_start_bar_index + 2: # At least 2 bars into session
                    current_session_range = ctx.bar.close - self.session_open
                    abs_current_session_range = abs(current_session_range)

                    # Get median range from a longer lookback
                    median_range = self._median_range(ctx.highs(self.lookback_period_median_range), ctx.lows(self.lookback_period_median_range), self.lookback_period_median_range)

                    if median_range is None:
                        return None

                    # Check for decisive move exceeding median range
                    if abs_current_session_range > median_range * self.min_session_move_factor:
                        # Ensure it's not just a single-bar spike - current bar must be in direction of overall session move
                        # And price should be near session high for long, or near session low for short
                        
                        # Long condition
                        if current_session_range > 0 and ctx.bar.close > self.session_open: # Upward move
                            # Current close should be near the session high
                            if (self.session_high - ctx.bar.close) / median_range < 0.2: # within 20% of median range from session high
                                self.last_trade_bar_index = ctx.bar_index
                                self.trade_made_this_session = True
                                return ctx.signal("long", confidence=0.75,
                                                  metadata={
                                                      "reason": "us_session_breakout_long",
                                                      "session_open": self.session_open,
                                                      "session_range": current_session_range,
                                                      "median_range": median_range,
                                                      "current_close": ctx.bar.close,
                                                      "session_high": self.session_high
                                                  })

                        # Short condition
                        elif current_session_range < 0 and ctx.bar.close < self.session_open: # Downward move
                            # Current close should be near the session low
                            if (ctx.bar.close - self.session_low) / median_range < 0.2: # within 20% of median range from session low
                                self.last_trade_bar_index = ctx.bar_index
                                self.trade_made_this_session = True
                                return ctx.signal("short", confidence=0.75,
                                                  metadata={
                                                      "reason": "us_session_breakout_short",
                                                      "session_open": self.session_open,
                                                      "session_range": current_session_range,
                                                      "median_range": median_range,
                                                      "current_close": ctx.bar.close,
                                                      "session_low": self.session_low
                                                  })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: You could implement adaptive logic here, e.g., adjust confidence based on recent performance.
        pass