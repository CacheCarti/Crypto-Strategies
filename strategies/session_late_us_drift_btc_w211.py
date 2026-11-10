from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionBreakout(Strategy):
    METADATA = {
        "name": "US Session Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-3 hours average hold
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 18 # UTC 18:00 (late US session)
        self.session_end_hour = 23   # UTC 23:00 (end of late US session for entry)
        self.exit_hour = 0           # UTC 00:00 (exit before next day)
        self.range_period = 20       # Period for median range calculation
        self.min_move_multiplier = 1.0 # Min move to qualify as breakout
        self.min_breakout_bars = 2   # Require move over multiple bars, not just one
        self.max_session_closes_above_open = 3 # Max consecutive closes above open to consider it a breakout, not exhausted
        self.max_session_closes_below_open = 3 # Max consecutive closes below open to consider it a breakout, not exhausted

        self.last_signal_bar_index = -self.warmup_bars # Cooldown mechanism
        self.cooldown_bars = 5

        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open = None
        self.session_start_bar_index = -1
        self.consecutive_bull_bars = 0
        self.consecutive_bear_bars = 0

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        ranges.sort()
        mid = len(ranges) // 2
        if len(ranges) % 2 == 0:
            return (ranges[mid - 1] + ranges[mid]) / 2
        else:
            return ranges[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset session data at the start of a new session day (UTC 00:00)
        if current_hour == self.exit_hour and self.session_start_bar_index != -1 and ctx.bar_index > self.session_start_bar_index:
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_open = None
            self.session_start_bar_index = -1
            self.consecutive_bull_bars = 0
            self.consecutive_bear_bars = 0

        # Update session high/low if within session hours
        if self.session_start_hour <= current_hour <= self.session_end_hour:
            if self.session_start_bar_index == -1: # First bar of the session
                self.session_open = ctx.bar.open
                self.session_start_bar_index = ctx.bar_index
                self.session_high = ctx.bar.high
                self.session_low = ctx.bar.low
            else:
                self.session_high = max(self.session_high, ctx.bar.high)
                self.session_low = min(self.session_low, ctx.bar.low)

            # Track consecutive closes for exhaustion check
            if ctx.bar.close > ctx.bar.open:
                self.consecutive_bull_bars += 1
                self.consecutive_bear_bars = 0
            elif ctx.bar.close < ctx.bar.open:
                self.consecutive_bear_bars += 1
                self.consecutive_bull_bars = 0
            else:
                self.consecutive_bull_bars = 0
                self.consecutive_bear_bars = 0

        # Exit logic
        if ctx.has_position():
            # Exit before next day's session starts
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

            # Check for structural stop (e.g., if price reverses significantly against the session move)
            if ctx.position_direction() == "long":
                if ctx.bar.close < self.session_open * (1 - self.METADATA["declared_sl_bps"] / 10000.0): # Simple trailing stop based on session open
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_structure_stop_reversal", "session_open": self.session_open, "current_close": ctx.bar.close})
            elif ctx.position_direction() == "short":
                if ctx.bar.close > self.session_open * (1 + self.METADATA["declared_sl_bps"] / 10000.0): # Simple trailing stop based on session open
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_structure_stop_reversal", "session_open": self.session_open, "current_close": ctx.bar.close})

            return None # Hold position if no exit condition met


        # Entry logic
        # Only consider entries during the defined session hours
        if not (self.session_start_hour <= current_hour <= self.session_end_hour):
            return None

        # Cooldown check
        if ctx.bar_index < self.last_signal_bar_index + self.cooldown_bars:
            return None

        median_range = self._median_range(highs, lows, self.range_period)
        if median_range is None:
            return None

        # Calculate session move relative to session open
        session_move = ctx.bar.close - self.session_open
        session_move_pct = (session_move / self.session_open) * 10000.0 # in bps

        min_move_threshold = median_range * self.min_move_multiplier

        # Long entry conditions
        # 1. Price has decisively moved above session open
        # 2. Move exceeds a recent median range
        # 3. Not exhausted (e.g., too many consecutive bullish bars)
        # 4. Not a single-bar spike (require at least min_breakout_bars since session start)
        if (session_move > 0 and
            abs(session_move) > min_move_threshold and
            self.consecutive_bull_bars <= self.max_session_closes_above_open and
            ctx.bar_index >= self.session_start_bar_index + self.min_breakout_bars):

            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "late_us_session_long_breakout",
                    "session_open": self.session_open,
                    "current_close": ctx.bar.close,
                    "session_move_bps": session_move_pct,
                    "median_range": median_range,
                    "consecutive_bull_bars": self.consecutive_bull_bars,
                    "bar_hour": current_hour
                }
            )

        # Short entry conditions
        # 1. Price has decisively moved below session open
        # 2. Move exceeds a recent median range
        # 3. Not exhausted (e.g., too many consecutive bearish bars)
        # 4. Not a single-bar spike (require at least min_breakout_bars since session start)
        elif (session_move < 0 and
              abs(session_move) > min_move_threshold and
              self.consecutive_bear_bars <= self.max_session_closes_below_open and
              ctx.bar_index >= self.session_start_bar_index + self.min_breakout_bars):

            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "late_us_session_short_breakout",
                    "session_open": self.session_open,
                    "current_close": ctx.bar.close,
                    "session_move_bps": session_move_pct,
                    "median_range": median_range,
                    "consecutive_bear_bars": self.consecutive_bear_bars,
                    "bar_hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close or adapt strategy based on performance
        # For simplicity, this strategy does not implement adaptive logic here.
        pass