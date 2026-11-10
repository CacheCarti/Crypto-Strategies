from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEuropeSessionHandoff(Strategy):
    METADATA = {
        "name": "ETH Europe Session Handoff",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 5,  # Up to 5 hours, covering the session handoff
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this price-action based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7
        self.entry_hour_end = 12   # UTC 12 (exclusive, so up to 11:59)
        self.exit_hour = 12        # UTC 12 (exit before/at this hour)
        self.impulse_period = 4    # Bars to define initial impulse (4 hours)
        self.follow_through_period = 1 # Bars for follow-through confirmation
        self.min_impulse_move_bps = 20 # Minimum impulse move to consider, in BPS
        self.max_entry_cooldown_bars = 6 # Cooldown after any trade (entry or exit)
        self.cooldown_counter = 0

        # Store the bar index of the last trade
        self.last_trade_bar_index = -self.max_entry_cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.max_entry_cooldown_bars:
            self.cooldown_counter += 1
            if ctx.has_position():
                # If in position, we still need to check exit conditions
                pass
            else:
                return None # Skip entry logic during cooldown

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: Close position at or before the exit hour
        if ctx.has_position() and current_hour >= self.exit_hour:
            direction = ctx.position_direction()
            entry_hour = ctx.position.get("metadata", {}).get("entry_hour")
            self.last_trade_bar_index = ctx.bar_index # Reset cooldown on exit
            return ctx.signal(
                "flat",
                confidence=0.9,
                metadata={
                    "reason": "session_boundary_exit",
                    "entry_hour": entry_hour,
                    "exit_hour": current_hour,
                    "current_price": ctx.bar.close,
                }
            )

        # Entry logic: Only consider entries within the specified European session window
        if not ctx.has_position() and self.entry_hour_start <= current_hour < self.entry_hour_end:
            # Check for enough bars for impulse and follow-through
            if len(closes) < self.impulse_period + self.follow_through_period:
                return None

            # Calculate impulse over the impulse_period
            impulse_start_price = closes[-self.impulse_period - self.follow_through_period]
            impulse_end_price = closes[-self.follow_through_period -1] # Price before follow-through period
            impulse_move_bps = (impulse_end_price - impulse_start_price) / impulse_start_price * 10000

            # Calculate follow-through over the follow_through_period
            follow_through_start_price = closes[-self.follow_through_period - 1]
            follow_through_end_price = ctx.bar.close
            follow_through_move_bps = (follow_through_end_price - follow_through_start_price) / follow_through_start_price * 10000

            signal_direction = None
            confidence = 0.0
            reason = ""

            # Long condition: Positive impulse, positive follow-through
            if impulse_move_bps > self.min_impulse_move_bps and follow_through_move_bps > 0:
                signal_direction = "long"
                confidence = min(0.7, abs(impulse_move_bps / 100)) # Scale confidence by impulse strength
                reason = "europe_session_long_impulse_follow_through"

            # Short condition: Negative impulse, negative follow-through
            elif impulse_move_bps < -self.min_impulse_move_bps and follow_through_move_bps < 0:
                signal_direction = "short"
                confidence = min(0.7, abs(impulse_move_bps / 100)) # Scale confidence by impulse strength
                reason = "europe_session_short_impulse_follow_through"

            if signal_direction:
                self.last_trade_bar_index = ctx.bar_index # Reset cooldown on entry
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "entry_hour": current_hour,
                        "impulse_move_bps": impulse_move_bps,
                        "follow_through_move_bps": follow_through_move_bps,
                        "current_price": ctx.bar.close,
                        "cooldown_counter_last_trade": self.cooldown_counter,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown counter when a position closes
        self.cooldown_counter = 0
        self.last_trade_bar_index = ctx.bar_index