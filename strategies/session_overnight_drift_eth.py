from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthOvernightDrift(Strategy):
    METADATA = {
        "name": "EthOvernightDrift",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hour holds
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 00:00
        self.session_end_hour = 6    # UTC 06:00
        self.trade_cooldown_bars = 12 # Wait 12 hours after a trade
        self.last_trade_bar = -self.trade_cooldown_bars * 2 # Initialize to allow immediate trade
        self.median_range_period = 30 # For recent median range
        self.min_session_move_factor = 0.005 # 0.5% move
        self.max_single_bar_spike_ratio = 0.6 # Max 60% of session move from a single bar
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open_price = None
        self.session_direction = None
        self.session_move_magnitude = 0.0

    def _median(self, values):
        if not values:
            return 0.0
        sorted_values = sorted(values)
        mid = len(sorted_values) // 2
        if len(sorted_values) % 2 == 0:
            return (sorted_values[mid - 1] + sorted_values[mid]) / 2
        else:
            return sorted_values[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_range_period + 1)
        highs = ctx.highs(self.median_range_period + 1)
        lows = ctx.lows(self.median_range_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Session reset logic
        if current_hour == self.session_start_hour and ctx.bar_index != self.last_trade_bar: # Reset at start of session, ensure not on same bar as last trade
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_open_price = ctx.bar.open
            self.session_direction = None
            self.session_move_magnitude = 0.0
        else:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Logic for detecting session move within the overnight window
        if self.session_start_hour <= current_hour < self.session_end_hour and self.session_open_price is not None:
            move = ctx.bar.close - self.session_open_price
            session_range = self.session_high - self.session_low
            
            if move > 0.0001 * self.session_open_price: # Some positive move
                self.session_direction = "long"
                self.session_move_magnitude = move
            elif move < -0.0001 * self.session_open_price: # Some negative move
                self.session_direction = "short"
                self.session_move_magnitude = abs(move)
            else:
                self.session_direction = None
                self.session_move_magnitude = 0.0

        # Entry logic: after session end, if conditions met
        if current_hour == self.session_end_hour and self.session_direction and not ctx.has_position():
            if ctx.bar_index - self.last_trade_bar < self.trade_cooldown_bars:
                return None

            # Calculate recent median range
            ranges = [highs[i] - lows[i] for i in range(1, len(highs))]
            median_range = self._median(ranges[-self.median_range_period:])
            
            if median_range == 0:
                return None

            # Check if session move is decisive
            move_pct = self.session_move_magnitude / self.session_open_price
            if move_pct < self.min_session_move_factor:
                return None # Not decisive enough

            # Check for single-bar spike (current bar's range vs total session move)
            current_bar_range = ctx.bar.high - ctx.bar.low
            if current_bar_range / self.session_move_magnitude > self.max_single_bar_spike_ratio:
                return None # Too much of the move came from a single bar

            # Check if current close confirms the direction (optional, but adds robustness)
            if self.session_direction == "long" and ctx.bar.close < self.session_open_price + (self.session_move_magnitude * 0.5):
                return None # Price pulled back too much
            if self.session_direction == "short" and ctx.bar.close > self.session_open_price - (self.session_move_magnitude * 0.5):
                return None # Price pulled back too much

            confidence = min(0.8, 0.5 + (move_pct / (self.min_session_move_factor * 2))) # Scale confidence
            
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                self.session_direction,
                confidence=confidence,
                metadata={
                    "reason": f"overnight_drift_follow_{self.session_direction}",
                    "session_open": self.session_open_price,
                    "session_move_pct": move_pct,
                    "median_range": median_range,
                    "current_price": ctx.bar.close
                }
            )

        # Exit logic: before next US session (e.g., UTC 12:00) or if structure breaks
        if ctx.has_position():
            if current_hour == 12: # Exit before US session opens fully
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "time_based_exit_pre_us_session", "current_hour": current_hour, "price": ctx.bar.close})

            # Simple structure stop: if price goes against entry significantly (e.g., 50% of session move)
            pos = ctx.position
            if pos:
                if pos["direction"] == "long":
                    if ctx.bar.close < pos["entry_price"] - (self.session_move_magnitude * 0.5):
                        return ctx.signal("flat", confidence=0.6, metadata={"reason": "structure_stop_long", "entry": pos["entry_price"], "current_price": ctx.bar.close, "session_move_mag": self.session_move_magnitude})
                elif pos["direction"] == "short":
                    if ctx.bar.close > pos["entry_price"] + (self.session_move_magnitude * 0.5):
                        return ctx.signal("flat", confidence=0.6, metadata={"reason": "structure_stop_short", "entry": pos["entry_price"], "current_price": ctx.bar.close, "session_move_mag": self.session_move_magnitude})
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already handle cooldown in on_bar, no specific action here
        pass