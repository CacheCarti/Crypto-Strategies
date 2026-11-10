from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class EthSessionDrift(Strategy):
    METADATA = {
        "name": "ETH US Session Drift",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, 3600s is 1 bar
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this price-action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 18 # UTC 18:00
        self.entry_hour_end = 23   # UTC 23:00 (meaning up to 23:59:59)
        self.exit_hour = 0         # UTC 0:00 (exit before this)

        self.median_range_period = 20 # Period for median true range
        self.min_move_multiplier = 0.5 # Minimum move as a fraction of median range
        self.max_move_multiplier = 2.0 # Max move to avoid exhausted moves

        self.last_trade_bar = -self.warmup_bars # Initialize to allow trading after warmup
        self.cooldown_bars = 5 # Cooldown after an exit before new entry

        # Store recent true ranges to calculate median
        self.true_ranges = collections.deque(maxlen=self.median_range_period)

    def _true_range(self, high: float, low: float, prev_close: float) -> float:
        return max(high - low, abs(high - prev_close), abs(low - prev_close))

    def _median(self, data: collections.deque) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(list(data))
        mid = len(sorted_data) // 2
        if len(sorted_data) % 2 == 0:
            return (sorted_data[mid - 1] + sorted_data[mid]) / 2
        else:
            return sorted_data[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        opens = ctx.opens(self.warmup_bars + 1)

        if len(closes) <= self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # Update true ranges for median calculation
        if len(closes) > 1:
            self.true_ranges.append(self._true_range(ctx.bar.high, ctx.bar.low, closes[-2]))
        
        median_tr = self._median(self.true_ranges)
        
        # Cooldown logic
        if current_bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Exit existing position before UTC 0:00
        if ctx.has_position() and current_hour == self.exit_hour:
            self.last_trade_bar = current_bar_index
            return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_close_time", "current_hour": current_hour})

        # Only consider entry if no position is open
        if ctx.has_position():
            return None

        # Entry logic within the specified session hours
        if self.entry_hour_start <= current_hour <= self.entry_hour_end:
            # Look back to the start of the session to calculate the move
            session_start_price = None
            session_high = -math.inf
            session_low = math.inf
            session_bars = 0

            # Iterate backwards to find the start of the current session
            for i in range(1, len(closes)):
                bar_idx = current_bar_index - i
                if bar_idx < 0: # Should not happen with warmup_bars check
                    break
                
                bar_hour = ctx.bar.timestamp.replace(hour=(current_hour - i)).hour # Approx hour for historical bar
                
                if not (self.entry_hour_start <= bar_hour <= self.entry_hour_end):
                    break # Found the start of the session or before it

                session_start_price = opens[-i] # The open of the first bar of the session
                session_high = max(session_high, highs[-i])
                session_low = min(session_low, lows[-i])
                session_bars += 1
            
            if session_start_price is None or session_bars < 2:
                # Not enough bars to form a session or calculate move
                return None

            current_price = closes[-1]
            session_move = current_price - session_start_price
            
            # Use median_tr to set dynamic thresholds for minimum and maximum move
            min_move = median_tr * self.min_move_multiplier
            max_move = median_tr * self.max_move_multiplier

            # Check for decisive but not exhausted move
            if median_tr > 0 and min_move < abs(session_move) < max_move:
                confidence = 0.6 + (abs(session_move) / max_move) * 0.3 # Scale confidence based on move strength

                if session_move > 0: # Bullish drift
                    self.last_trade_bar = current_bar_index
                    return ctx.signal("long", confidence=confidence,
                                      metadata={"reason": "session_drift_long",
                                                "entry_hour": current_hour,
                                                "session_move_abs": abs(session_move),
                                                "median_true_range": median_tr,
                                                "min_move_threshold": min_move,
                                                "max_move_threshold": max_move})
                elif session_move < 0: # Bearish drift
                    self.last_trade_bar = current_bar_index
                    return ctx.signal("short", confidence=confidence,
                                      metadata={"reason": "session_drift_short",
                                                "entry_hour": current_hour,
                                                "session_move_abs": abs(session_move),
                                                "median_true_range": median_tr,
                                                "min_move_threshold": min_move,
                                                "max_move_threshold": max_move})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already set last_trade_bar on signal. This can be used for adaptive logic if needed.
        pass