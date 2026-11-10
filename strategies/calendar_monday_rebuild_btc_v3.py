from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MondayRebuildBTC(Strategy):
    METADATA = {
        "name": "MondayRebuildBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # Aim for a few hours, but can be longer
        "warmup_bars": 72, # Enough for 3 days of hourly data (72 bars)
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 24 # Cooldown for 24 hours after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.last_monday_signal_bar = -self.cooldown_bars # Track last Monday signal to prevent repeated entries

        # Parameters for detecting weekend move and Monday rebuild
        self.weekend_period = 48 # Check last 48 hours (Fri-Sun) for weekend move
        self.rebuild_period = 12 # Look at 12 hours of Monday for confirmation
        self.min_weekend_move_pct = 0.005 # 0.5% move over the weekend
        self.min_monday_rebuild_pct = 0.002 # 0.2% move on Monday for confirmation

        # To avoid overtrading on continuous conditions, we need to detect transitions
        self.last_weekend_direction = None # "up", "down", or None
        self.last_monday_rebuild_attempt_bar = -1

    def _calculate_change_pct(self, closes, period):
        if len(closes) < period:
            return 0.0
        return (closes[-1] - closes[-period]) / closes[-period]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1) # Need enough for weekend_period + rebuild_period
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Only consider trades on Monday
        if ctx.bar.timestamp.weekday() != 0: # Monday is 0
            self.last_weekend_direction = None # Reset weekend direction on non-Monday
            return None

        # Only consider trading during US market hours for confirmation (e.g., 9 AM to 5 PM UTC)
        # Assuming UTC for simplicity, adjust for EST/EDT if needed by user
        if not (9 <= ctx.bar.timestamp.hour < 17):
            return None

        # --- Weekend Move Detection (from Friday close to Sunday close) ---
        # Need at least 48 bars for the weekend period
        if len(closes) < self.weekend_period:
            return None

        # This logic should ideally look at Friday close vs Sunday close
        # For simplicity, we'll approximate as the move over the last 'weekend_period' bars
        # This will capture the general direction leading into Monday
        weekend_closes = ctx.closes(self.weekend_period)
        if len(weekend_closes) < self.weekend_period:
            return None

        weekend_start_price = weekend_closes[0]
        weekend_end_price = weekend_closes[-1] # This is the price at the start of Monday's observation window
        weekend_move_pct = (weekend_end_price - weekend_start_price) / weekend_start_price

        current_weekend_direction = None
        if weekend_move_pct > self.min_weekend_move_pct:
            current_weekend_direction = "up"
        elif weekend_move_pct < -self.min_weekend_move_pct:
            current_weekend_direction = "down"

        # Only act if the weekend direction has changed or is new, to prevent repeated signals
        # and if we haven't attempted a rebuild trade on this Monday yet
        if current_weekend_direction is None or \
           (current_weekend_direction == self.last_weekend_direction and ctx.bar_index == self.last_monday_rebuild_attempt_bar):
            return None

        # --- Monday Rebuild Confirmation ---
        # Look at the price action on Monday (up to current bar)
        # This assumes we are past the warmup for Monday's bars (e.g., 12 bars into Monday)
        monday_closes = closes[-(ctx.bar.timestamp.hour + 1):] # Closes from midnight Monday to current bar
        if len(monday_closes) < self.rebuild_period: # Need enough bars for rebuild confirmation
            return None

        monday_start_price = monday_closes[0]
        monday_current_price = monday_closes[-1]
        monday_rebuild_pct = (monday_current_price - monday_start_price) / monday_start_price

        signal_direction = None
        reason = ""
        confidence = 0.0

        # If weekend was down, look for Monday rebuild up
        if current_weekend_direction == "down" and monday_rebuild_pct > self.min_monday_rebuild_pct:
            if not ctx.has_position():
                signal_direction = "long"
                reason = "monday_rebuild_long"
                confidence = min(0.5 + abs(weekend_move_pct) * 10, 0.9) # Scale confidence by weekend move size
        # If weekend was up, look for Monday rebuild down
        elif current_weekend_direction == "up" and monday_rebuild_pct < -self.min_monday_rebuild_pct:
            if not ctx.has_position():
                signal_direction = "short"
                reason = "monday_rebuild_short"
                confidence = min(0.5 + abs(weekend_move_pct) * 10, 0.9)

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            self.last_weekend_direction = current_weekend_direction
            self.last_monday_rebuild_attempt_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "weekend_move_pct": weekend_move_pct,
                    "monday_rebuild_pct": monday_rebuild_pct,
                    "current_hour": ctx.bar.timestamp.hour,
                    "weekend_direction": current_weekend_direction,
                }
            )

        # If we have a position, don't signal again until it's closed, unless we want to flat
        if ctx.has_position():
            # For this strategy, we only want one position per Monday rebuild event.
            # We'll rely on the declared SL/TP or horizon to close it.
            return None

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_weekend_direction to allow a new signal on the *next* Monday
        # This prevents the strategy from trying to rebuild the same weekend move multiple times
        self.last_weekend_direction = None
        self.last_monday_rebuild_attempt_bar = -1 # Reset for next Monday
        pass