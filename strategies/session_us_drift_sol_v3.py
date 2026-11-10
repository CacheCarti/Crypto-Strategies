from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class USSessionDriftSOL(Strategy):
    METADATA = {
        "name": "US Session Drift SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours, to capture drift
        "warmup_bars": 50,
        "required_features": [], # No specific features required for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13  # UTC 13:00
        self.us_session_end_hour = 21    # UTC 21:00
        self.exit_hour = 21             # Exit before 21:00 UTC
        self.lookback_period = 20        # For average range
        self.min_move_ratio = 1.0        # Min move relative to median range
        self.max_move_ratio = 3.0        # Max move relative to median range (avoid exhaustion)
        self.cooldown_bars = 5           # Cooldown after a trade
        self.last_signal_bar = -self.cooldown_bars
        self.session_open_price = None
        self.session_high = None
        self.session_low = None
        self.session_move_direction = None
        self.daily_trade_fired = False # Ensure only one trade per day/session
        self.last_day = -1

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        if not ranges:
            return 0.0
        ranges.sort()
        mid = len(ranges) // 2
        if len(ranges) % 2 == 0:
            return (ranges[mid - 1] + ranges[mid]) / 2
        else:
            return ranges[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset daily trade flag at the start of a new day
        if current_day != self.last_day:
            self.daily_trade_fired = False
            self.last_day = current_day

        # Check for cooldown
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Session boundary logic
        if current_hour == self.us_session_start_hour:
            # New US session starts
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_move_direction = None
            # Reset daily trade flag for the new session, even if same day
            self.daily_trade_fired = False
        elif self.session_open_price is not None and current_hour > self.us_session_start_hour:
            # Within the US session, update high/low
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Exit existing position before the exit hour
        if ctx.has_position() and current_hour >= self.exit_hour:
            position_data = ctx.position
            return ctx.signal("flat", confidence=0.8, metadata={
                "reason": "exit_before_session_close",
                "current_hour": current_hour,
                "position_direction": position_data["direction"],
                "entry_price": position_data["entry_price"]
            })

        # Entry logic: after session start, before exit hour, and no position
        if not ctx.has_position() and not self.daily_trade_fired and \
           current_hour >= self.us_session_start_hour + 1 and current_hour < self.exit_hour:

            if self.session_open_price is None: # Should not happen if start hour logic works
                return None

            median_daily_range = self._median_range(highs, lows, self.lookback_period)
            if median_daily_range is None or median_daily_range == 0:
                return None

            current_session_move = ctx.bar.close - self.session_open_price
            abs_session_move = abs(current_session_move)

            # Avoid single bar spikes by checking if the move is recent but not *just* from the last bar
            # We are looking for a sustained move *from* the session open, not just current bar's move.
            # This is implicitly handled by using session_open_price.

            # Check if current session move is within the desired range
            min_threshold = median_daily_range * self.min_move_ratio
            max_threshold = median_daily_range * self.max_move_ratio

            if abs_session_move >= min_threshold and abs_session_move <= max_threshold:
                signal_direction = None
                reason = ""
                
                # Check for a decisive move and if it's the first time detecting this direction for the session
                if current_session_move > 0 and self.session_move_direction != "long":
                    signal_direction = "long"
                    reason = "us_session_drift_long"
                    self.session_move_direction = "long" # Mark direction as detected
                elif current_session_move < 0 and self.session_move_direction != "short":
                    signal_direction = "short"
                    reason = "us_session_drift_short"
                    self.session_move_direction = "short" # Mark direction as detected

                if signal_direction:
                    self.last_signal_bar = ctx.bar_index
                    self.daily_trade_fired = True
                    return ctx.signal(
                        signal_direction,
                        confidence=0.7,
                        metadata={
                            "reason": reason,
                            "session_open_price": self.session_open_price,
                            "current_close": ctx.bar.close,
                            "session_move": current_session_move,
                            "median_range": median_daily_range,
                            "min_threshold": min_threshold,
                            "max_threshold": max_threshold,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close to allow for new entries faster if conditions are met
        # Alternatively, could enforce a longer cooldown here if desired.
        pass