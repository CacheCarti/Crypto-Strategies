from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionDrift(Strategy):
    METADATA = {
        "name": "SolSessionDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 30,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20  # For median range calculation
        self.cooldown_bars = 12  # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.entry_threshold_multiplier = 0.8  # Percentage of median range for a "decisive" move
        self.exhaustion_threshold_multiplier = 1.5 # If move exceeds this, it's exhausted
        self.max_session_trades = 1
        self.trades_today = 0
        self.last_bar_hour = -1

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
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.lookback_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset daily trade count at start of new day (UTC 0)
        if current_hour == 0 and self.last_bar_hour != 0:
            self.trades_today = 0
        self.last_bar_hour = current_hour

        # Exit any existing position before UTC 0
        if ctx.has_position() and current_hour == 23: # Consider exiting at 23:00 UTC to be flat by 00:00 UTC
            if ctx.position_direction() == "long":
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_price": ctx.bar.close})
            elif ctx.position_direction() == "short":
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_price": ctx.bar.close})

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate recent median true range
        true_ranges = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            true_ranges.append(tr)
        
        median_range = self._median(true_ranges[-self.lookback_period:])
        if median_range == 0: # Avoid division by zero
            return None

        # Check for late US session (UTC 18-24)
        if 18 <= current_hour <= 23 and not ctx.has_position() and self.trades_today < self.max_session_trades:
            # Calculate the move during the current bar
            bar_move = ctx.bar.close - ctx.bar.open
            bar_move_abs = abs(bar_move)

            # Avoid single-bar spikes - ensure the move is not too extreme for one bar
            # This is a heuristic, adjust as needed
            if bar_move_abs > self.exhaustion_threshold_multiplier * median_range:
                return None # Move is too exhausted or too spiky

            # Decisive move check
            if bar_move > self.entry_threshold_multiplier * median_range: # Decisive upward move
                self.last_trade_bar = ctx.bar_index
                self.trades_today += 1
                return ctx.signal("long", confidence=0.8,
                                  metadata={"reason": "late_us_drift_long",
                                            "current_hour": current_hour,
                                            "bar_move": bar_move,
                                            "median_range": median_range,
                                            "entry_threshold": self.entry_threshold_multiplier * median_range,
                                            "close": ctx.bar.close})

            elif bar_move < -self.entry_threshold_multiplier * median_range: # Decisive downward move
                self.last_trade_bar = ctx.bar_index
                self.trades_today += 1
                return ctx.signal("short", confidence=0.8,
                                  metadata={"reason": "late_us_drift_short",
                                            "current_hour": current_hour,
                                            "bar_move": bar_move,
                                            "median_range": median_range,
                                            "entry_threshold": self.entry_threshold_multiplier * median_range,
                                            "close": ctx.bar.close})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We can log position details here if needed
        # self.last_trade_bar is already updated on signal.
        pass