from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcOvernightSessionImpulse(Strategy):
    METADATA = {
        "name": "BTC Overnight Session Impulse",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Max hold 6 hours (0-6 UTC)
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 0  # UTC 00:00
        self.entry_hour_end = 6    # UTC 06:00
        self.exit_hour = 6         # UTC 06:00 (end of overnight session)
        self.min_session_bars = 4  # Minimum bars to establish impulse
        self.momentum_period = 4   # For short-term impulse
        self.cooldown_bars = 6     # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # Cooldown logic
        if current_bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit logic: Flat position at the end of the overnight session
        if ctx.has_position() and current_hour == self.exit_hour:
            self.last_trade_bar = current_bar_index
            return ctx.signal(
                "flat",
                confidence=0.8,
                metadata={
                    "reason": "session_handoff_exit",
                    "current_hour": current_hour,
                    "bar_index": current_bar_index
                }
            )

        # Entry logic: Only consider entries during the overnight session (0-6 UTC)
        if current_hour >= self.entry_hour_start and current_hour < self.entry_hour_end:
            if ctx.has_position():
                return None # Already in a position, wait for exit

            # Ensure enough bars have passed in the session to measure impulse
            # This logic assumes the session starts at 0 UTC and we're looking at consecutive bars
            # If the strategy was more complex, we might track session start more explicitly
            session_bars_elapsed = (current_hour - self.entry_hour_start) + 1 # +1 for current bar
            if session_bars_elapsed < self.min_session_bars:
                return None

            # Calculate session impulse using Z-score of recent price action
            # We need at least momentum_period bars for z-score calculation
            if len(closes) < self.momentum_period:
                return None

            z_score = self._zscore(closes, self.momentum_period)

            if z_score is None:
                return None

            signal_direction = None
            confidence = 0.0
            reason = ""

            # Long entry condition: Strong positive impulse
            if z_score > 1.0: # Z-score > 1 means price is 1 std dev above its recent mean
                signal_direction = "long"
                confidence = min(0.7 + (z_score - 1.0) * 0.1, 0.9) # Scale confidence with z-score
                reason = "session_impulse_long"

            # Short entry condition: Strong negative impulse
            elif z_score < -1.0: # Z-score < -1 means price is 1 std dev below its recent mean
                signal_direction = "short"
                confidence = min(0.7 + (abs(z_score) - 1.0) * 0.1, 0.9) # Scale confidence with z-score
                reason = "session_impulse_short"

            if signal_direction:
                self.last_trade_bar = current_bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "z_score": z_score,
                        "momentum_period": self.momentum_period,
                        "current_hour": current_hour,
                        "session_bars_elapsed": session_bars_elapsed,
                        "close": ctx.bar.close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt strategy based on close outcome
        # For this simple strategy, we just log the close
        pass