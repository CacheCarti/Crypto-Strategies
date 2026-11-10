from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightSessionBreakout(Strategy):
    METADATA = {
        "name": "Overnight Session Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # ~3 hours intended hold
        "warmup_bars": 50,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 00:00 start of overnight session
        self.session_end_hour = 6    # UTC 06:00 end of overnight session
        self.entry_cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.entry_cooldown_bars # Initialize to allow immediate trade

        # Parameters for median range calculation
        self.range_period = 20 # Lookback for median range
        self.breakout_multiplier = 0.75 # How much of the median range to break
        self.spike_filter_ratio = 0.7 # Ratio of single bar close to range to filter spikes

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        sorted_ranges = sorted(ranges)
        mid = len(sorted_ranges) // 2
        if len(sorted_ranges) % 2 == 0:
            return (sorted_ranges[mid - 1] + sorted_ranges[mid]) / 2
        else:
            return sorted_ranges[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.entry_cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Only consider trades around the session boundary
        if current_hour != self.session_end_hour:
            return None

        # Need enough bars for range calculation
        if len(ctx.highs(self.range_period + self.session_end_hour + 1)) < self.range_period + self.session_end_hour + 1:
            return None

        # Get session data (00:00 to 06:00 UTC, which is 7 bars including 00:00)
        session_closes = ctx.closes(self.session_end_hour - self.session_start_hour + 1)
        session_opens = ctx.opens(self.session_end_hour - self.session_start_hour + 1)
        session_highs = ctx.highs(self.session_end_hour - self.session_start_hour + 1)
        session_lows = ctx.lows(self.session_end_hour - self.session_start_hour + 1)

        if len(session_closes) < (self.session_end_hour - self.session_start_hour + 1):
            return None # Not enough session bars yet

        session_open = session_opens[0]
        session_close = session_closes[-1]
        session_high = max(session_highs)
        session_low = min(session_lows)

        session_range = session_high - session_low
        session_move = session_close - session_open

        median_range = self._median_range(ctx.highs(self.range_period), ctx.lows(self.range_period), self.range_period)
        if median_range is None:
            return None

        # Filter out single-bar spikes
        current_bar_range = ctx.bar.high - ctx.bar.low
        if current_bar_range > median_range * self.spike_filter_ratio and abs(session_move) < current_bar_range * self.spike_filter_ratio:
            return None # Current bar is a significant part of the move, potentially a spike

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Long breakout condition
        if session_move > 0 and session_move >= median_range * self.breakout_multiplier:
            if not ctx.has_position():
                signal_direction = "long"
                confidence = min(0.8, abs(session_move) / median_range) # Scale confidence by strength of move
                reason = "overnight_session_long_breakout"
        # Short breakout condition
        elif session_move < 0 and abs(session_move) >= median_range * self.breakout_multiplier:
            if not ctx.has_position():
                signal_direction = "short"
                confidence = min(0.8, abs(session_move) / median_range) # Scale confidence by strength of move
                reason = "overnight_session_short_breakout"

        if ctx.has_position():
            # If we are in a position, check if it's time to exit before the next US session or if conditions reverse
            # Exit before next US session (e.g., 13:00 UTC)
            if current_hour == 12: # One hour before 13:00 UTC
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "exit_before_us_session",
                    "current_hour": current_hour,
                    "session_move": session_move,
                    "median_range": median_range
                })
            # Or if a strong opposing signal appears (simple reversal check)
            if (ctx.position_direction() == "long" and session_move < 0 and abs(session_move) >= median_range * self.breakout_multiplier * 0.5) or \
               (ctx.position_direction() == "short" and session_move > 0 and session_move >= median_range * self.breakout_multiplier * 0.5):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "exit_opposing_session_move",
                    "current_hour": current_hour,
                    "session_move": session_move,
                    "median_range": median_range
                })

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "session_open": session_open,
                    "session_close": session_close,
                    "session_move": session_move,
                    "median_range": median_range,
                    "breakout_multiplier": self.breakout_multiplier,
                    "current_price": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close
        self.last_trade_bar = ctx.bar_index
        # You could also log position details or adapt parameters here if needed
        pass