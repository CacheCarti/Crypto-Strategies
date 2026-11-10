from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionAsiaDriftETH(Strategy):
    METADATA = {
        "name": "SessionAsiaDriftETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": [], # No specific features required for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_session_start_hour = 0
        self.asia_session_end_hour = 8 # UTC 0-8
        self.min_session_move_pct = 0.5 # Minimum move percentage for a decisive session
        self.max_session_move_pct = 3.0 # Maximum move percentage to avoid exhausted moves
        self.min_median_range_multiplier = 1.2 # Session move must exceed median range by this factor
        self.median_range_period = 20 # Bars for median range calculation
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade
        self.last_trade_bar_index = -self.cooldown_bars # Initialize to allow trade on first bar

        # To track session open/close prices
        self.session_open_price = None
        self.session_bar_count = 0
        self.session_direction = None # "long" or "short"

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Calculate median daily range for context
        if len(highs) >= self.median_range_period and len(lows) >= self.median_range_period:
            daily_ranges = [h - l for h, l in zip(highs[-self.median_range_period:], lows[-self.median_range_period:])]
            median_range = self._median(daily_ranges)
        else:
            median_range = 0.0 # Not enough data

        # Check if we are in the Asia session
        if self.asia_session_start_hour <= current_hour < self.asia_session_end_hour:
            if self.session_open_price is None: # First bar of Asia session
                self.session_open_price = ctx.bar.open
                self.session_bar_count = 1
                self.session_direction = None # Reset direction for new session
            else:
                self.session_bar_count += 1
        else: # Outside Asia session
            # Process session data if we just exited Asia session and have collected data
            if self.session_open_price is not None and self.session_bar_count > 1: # Ensure session had multiple bars
                session_close_price = ctx.closes(1)[0] # Current close is the session close
                session_move_abs = session_close_price - self.session_open_price
                session_move_pct = (session_move_abs / self.session_open_price) * 100.0

                if abs(session_move_pct) >= self.min_session_move_pct and \
                   abs(session_move_pct) <= self.max_session_move_pct and \
                   median_range > 0 and \
                   abs(session_move_abs) > (median_range * self.min_median_range_multiplier):

                    if session_move_pct > 0:
                        self.session_direction = "long"
                    elif session_move_pct < 0:
                        self.session_direction = "short"
                else:
                    self.session_direction = None # Not a decisive move

            # Reset session data
            self.session_open_price = None
            self.session_bar_count = 0

            # Trading logic: enter based on prior session's direction
            if not ctx.has_position() and self.session_direction is not None:
                if ctx.bar_index >= self.last_trade_bar_index + self.cooldown_bars:
                    direction = self.session_direction
                    self.last_trade_bar_index = ctx.bar_index
                    
                    reason = f"asia_session_drift_{direction}"
                    metadata = {
                        "reason": reason,
                        "session_move_pct": session_move_pct,
                        "median_range": median_range,
                        "current_price": ctx.bar.close,
                        "session_open": self.session_open_price,
                    }
                    return ctx.signal(direction, confidence=0.7, metadata=metadata)
            
            # Exit logic: if we are in a position and it's approaching the next Asia session
            if ctx.has_position() and current_hour == self.asia_session_end_hour - 1: # Last hour before Asia session ends (or next one starts)
                 # Exit before next Asia session, or if current position is still from previous drift
                if ctx.position_direction() == self.session_direction: # Check if current position matches the last session's drift
                    metadata = {
                        "reason": "exit_before_next_asia_session",
                        "current_hour": current_hour,
                        "current_price": ctx.bar.close,
                        "entry_price": ctx.position["entry_price"]
                    }
                    return ctx.signal("flat", confidence=0.5, metadata=metadata)

        # If a position exists and we are out of the relevant session hours, or no new signal
        if ctx.has_position():
            return ctx.signal("hold")
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session direction after a position closes, in case it closed early
        self.session_direction = None
        # Cooldown is handled in on_bar by checking self.last_trade_bar_index
        pass