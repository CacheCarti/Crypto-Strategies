from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class GapReversionUSSession(Strategy):
    METADATA = {
        "name": "GapReversionUSSession",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours
        "warmup_bars": 20,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_us_session_close = None
        self.last_trade_bar_index = -math.inf
        self.cooldown_bars = 5 # Cooldown for 5 bars after a trade
        self.gap_threshold_bps = 20 # Minimum gap to consider, 20 bps
        self.continuation_threshold_bps = 5 # How much continuation is considered 'lack of continuation'
        self.max_session_hold_bars = 5 # Roughly 5 hours for 1h bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # --- Manage last US session close ---
        # The US session is defined as UTC 18-24.
        # We need the close of the *previous* US session, which would be the bar at hour 23 (or 00 UTC of next day)
        # So, if it's the first bar after UTC 0, we can capture the previous day's US session close.
        if current_hour == 0 and ctx.bar_index > 0:
            # The close of the bar at hour 23 (previous day) is the US session close
            # We need to ensure we only capture it once per day
            if self.last_us_session_close is None or ctx.bar.timestamp.day != self.last_us_session_close_timestamp.day:
                self.last_us_session_close = ctx.closes(2)[-2] # Close of the bar before current
                self.last_us_session_close_timestamp = ctx.bar.timestamp

        # --- Exit logic for existing position ---
        if ctx.has_position():
            # Exit if we are past the intended session hold
            bars_held = current_bar_index - self.position_entry_bar_index
            if bars_held >= self.max_session_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "max_session_hold_reached",
                    "bars_held": bars_held,
                    "entry_price": ctx.position["entry_price"],
                    "current_price": ctx.bar.close
                })
            # Exit before UTC 0 to avoid holding overnight into a new session calculation
            if current_hour == 23: # Last bar of the US session
                 return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "end_of_us_session_exit",
                    "bars_held": bars_held,
                    "entry_price": ctx.position["entry_price"],
                    "current_price": ctx.bar.close
                })
            return None # Hold position if no exit condition met

        # --- Entry logic ---
        # Only consider entries during the late US session (UTC 18-23)
        if not (18 <= current_hour <= 23):
            return None

        # Cooldown check
        if current_bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        if self.last_us_session_close is None:
            return None

        # Calculate gap from previous US session close to current bar open
        gap_to_open_bps = ((ctx.bar.open - self.last_us_session_close) / self.last_us_session_close) * 10000

        # Calculate continuation from current bar open to current bar close
        continuation_bps = ((ctx.bar.close - ctx.bar.open) / ctx.bar.open) * 10000

        signal_direction = None
        reason = ""
        confidence = 0.0

        # Long entry: Significant negative gap, but price fails to continue down (or reverses up)
        if gap_to_open_bps < -self.gap_threshold_bps and continuation_bps > -self.continuation_threshold_bps:
            signal_direction = "long"
            reason = "negative_gap_reversion"
            confidence = 0.75

        # Short entry: Significant positive gap, but price fails to continue up (or reverses down)
        elif gap_to_open_bps > self.gap_threshold_bps and continuation_bps < self.continuation_threshold_bps:
            signal_direction = "short"
            reason = "positive_gap_reversion"
            confidence = 0.75

        if signal_direction:
            self.last_trade_bar_index = current_bar_index
            self.position_entry_bar_index = current_bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "gap_to_open_bps": gap_to_open_bps,
                    "continuation_bps": continuation_bps,
                    "us_session_close": self.last_us_session_close,
                    "current_open": ctx.bar.open,
                    "current_close": ctx.bar.close,
                    "current_hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any state if needed, or log for adaptive sizing
        pass