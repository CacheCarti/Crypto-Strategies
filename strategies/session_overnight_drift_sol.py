from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class SessionOvernightDriftSOL(Strategy):
    METADATA = {
        "name": "SessionOvernightDriftSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 48, # Need 24 bars for 24-hr median range, plus a few for EMA
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_cooldown_bars = 12 # Cooldown after a trade
        self.bars_since_last_entry = self.entry_cooldown_bars
        self.overnight_start_hour = 0  # UTC 00:00
        self.overnight_end_hour = 6    # UTC 06:00 (exclusive, so 05:59 is last bar)
        self.exit_hour = 12           # Exit before US session gets fully active
        self.range_period = 24        # Bars for median range calculation (24 hours)
        self.ema_period = 12          # EMA for trend confirmation
        self.min_move_multiplier = 1.0 # Minimum move as a multiple of median range
        self.max_move_multiplier = 3.0 # Max move to avoid exhaustion
        self.overnight_session_data = {
            "start_price": None,
            "high": None,
            "low": None,
            "triggered_signal": False
        }

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = values[0] # Use first value as initial EMA
        for i in range(1, len(values)):
            ema = values[i] * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_last_entry += 1

        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Calculate median daily range for the last `range_period` bars
        if len(highs) < self.range_period or len(lows) < self.range_period:
            return None
        
        daily_ranges = [highs[i] - lows[i] for i in range(len(highs) - self.range_period, len(highs))]
        if not daily_ranges:
            return None
        median_range = statistics.median(daily_ranges)
        
        # Trend filter (EMA)
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # --- Session Management ---
        # Reset session data at the start of the overnight window
        if current_hour == self.overnight_start_hour and ctx.bar.timestamp.minute == 0:
            self.overnight_session_data = {
                "start_price": ctx.bar.open,
                "high": ctx.bar.high,
                "low": ctx.bar.low,
                "triggered_signal": False
            }
        
        # Update session high/low if within the overnight window
        if self.overnight_start_hour <= current_hour < self.overnight_end_hour:
            if self.overnight_session_data["start_price"] is not None:
                self.overnight_session_data["high"] = max(self.overnight_session_data["high"], ctx.bar.high)
                self.overnight_session_data["low"] = min(self.overnight_session_data["low"], ctx.bar.low)

        # --- Exit Logic ---
        if ctx.has_position():
            # Exit before next US session
            if current_hour == self.exit_hour and ctx.bar.timestamp.minute == 0:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_exit_time", "current_hour": current_hour})
            
            # Additional stop based on structure (e.g., break below session low for long, above session high for short)
            # This is a soft stop, the engine's declared_sl_bps is the hard stop.
            if ctx.position_direction() == "long":
                if ctx.bar.close < self.overnight_session_data["low"] and self.overnight_session_data["low"] is not None:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_structure_stop_long", "current_close": ctx.bar.close, "session_low": self.overnight_session_data["low"]})
            elif ctx.position_direction() == "short":
                if ctx.bar.close > self.overnight_session_data["high"] and self.overnight_session_data["high"] is not None:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_structure_stop_short", "current_close": ctx.bar.close, "session_high": self.overnight_session_data["high"]})

            # If already in a position, just hold
            return ctx.signal("hold")

        # --- Entry Logic ---
        # Only consider entry if within the overnight window AND not already triggered this session
        if self.overnight_start_hour <= current_hour < self.overnight_end_hour and \
           not self.overnight_session_data["triggered_signal"] and \
           self.bars_since_last_entry >= self.entry_cooldown_bars:

            start_price = self.overnight_session_data["start_price"]
            if start_price is None:
                return None

            session_high = self.overnight_session_data["high"]
            session_low = self.overnight_session_data["low"]

            # Calculate the move magnitude from session start
            move_up = ctx.bar.close - start_price
            move_down = start_price - ctx.bar.close

            # Check if the move is decisive (exceeds median range) but not exhausted
            min_decisive_move = median_range * self.min_move_multiplier
            max_tolerable_move = median_range * self.max_move_multiplier

            # Long entry condition
            if move_up > min_decisive_move and move_up < max_tolerable_move and \
               ctx.bar.close > ema and \
               ctx.bar.close > ctx.bar.open: # Close above open for bullish momentum
                
                self.overnight_session_data["triggered_signal"] = True
                self.bars_since_last_entry = 0
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "overnight_drift_long",
                        "current_hour": current_hour,
                        "session_start_price": start_price,
                        "current_price": ctx.bar.close,
                        "move_up_pct": (move_up / start_price) * 100,
                        "median_range": median_range,
                        "ema": ema
                    }
                )

            # Short entry condition
            if move_down > min_decisive_move and move_down < max_tolerable_move and \
               ctx.bar.close < ema and \
               ctx.bar.close < ctx.bar.open: # Close below open for bearish momentum

                self.overnight_session_data["triggered_signal"] = True
                self.bars_since_last_entry = 0
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "overnight_drift_short",
                        "current_hour": current_hour,
                        "session_start_price": start_price,
                        "current_price": ctx.bar.close,
                        "move_down_pct": (move_down / start_price) * 100,
                        "median_range": median_range,
                        "ema": ema
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after any position closes
        self.bars_since_last_entry = 0
        # Optional: adaptive sizing based on position['return_bps']
        # For this strategy, we keep it simple and just reset cooldown.