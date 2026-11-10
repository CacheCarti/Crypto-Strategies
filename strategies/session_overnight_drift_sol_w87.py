from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class OvernightSessionBreakout(Strategy):
    METADATA = {
        "name": "OvernightSessionBreakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # Aim for 2-hour hold, exit before next session
        "warmup_bars": 50,
        "required_features": [], # No specific features required for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 00:00
        self.session_end_hour = 6    # UTC 06:00
        self.exit_hour = 12          # UTC 12:00, before US session (13:00-14:00 UTC start)
        self.min_session_move_factor = 0.005 # 0.5% move required
        self.median_range_period = 20 # Bars for median range calculation
        self.cooldown_bars = 5       # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store session data
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_start_price = None
        self.session_volume = 0.0
        self.session_active = False
        self.last_session_day = -1

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
        closes = ctx.closes(self.median_range_period + 1)
        highs = ctx.highs(self.median_range_period + 1)
        lows = ctx.lows(self.median_range_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Check for new session day
        if current_day != self.last_session_day:
            self.last_session_day = current_day
            # Reset session data at the start of a new day
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_start_price = None
            self.session_volume = 0.0
            self.session_active = False # Ensure session starts fresh

        # Session tracking (UTC 00:00 to 06:00)
        if current_hour >= self.session_start_hour and current_hour < self.session_end_hour:
            if not self.session_active:
                # Start of a new session window
                self.session_start_price = ctx.bar.open
                self.session_high = ctx.bar.high
                self.session_low = ctx.bar.low
                self.session_volume = ctx.bar.volume
                self.session_active = True
            else:
                # Update session high/low/volume
                self.session_high = max(self.session_high, ctx.bar.high)
                self.session_low = min(self.session_low, ctx.bar.low)
                self.session_volume += ctx.bar.volume
        elif self.session_active and current_hour >= self.session_end_hour:
            # Session window just ended, evaluate for a breakout
            self.session_active = False # Mark session as inactive after evaluation

            if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
                return None # Cooldown active

            if self.session_start_price is None:
                return None

            session_range = self.session_high - self.session_low
            session_move_abs = abs(closes[-1] - self.session_start_price)
            session_move_pct = session_move_abs / self.session_start_price

            # Calculate recent median range
            daily_ranges = [h - l for h, l in zip(highs[-self.median_range_period-1:-1], lows[-self.median_range_period-1:-1])]
            median_daily_range = self._median(daily_ranges)

            # Check for decisive move and not exhausted
            # Decisive move: session move percentage exceeds a threshold and is larger than a fraction of recent median range
            # Not exhausted: current price is still moving in the session direction, not reversing
            
            if session_move_pct > self.min_session_move_factor and session_range > 0 and session_move_abs > (median_daily_range * 0.5): # At least 50% of median range
                
                # Long breakout if price closed above session high
                if closes[-1] > self.session_high:
                    if not ctx.has_position():
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "overnight_session_bullish_breakout",
                                "session_start_price": self.session_start_price,
                                "session_high": self.session_high,
                                "session_low": self.session_low,
                                "session_move_pct": round(session_move_pct * 100, 2),
                                "median_daily_range": round(median_daily_range, 2),
                                "current_close": closes[-1]
                            }
                        )
                # Short breakout if price closed below session low
                elif closes[-1] < self.session_low:
                    if not ctx.has_position():
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            metadata={
                                "reason": "overnight_session_bearish_breakout",
                                "session_start_price": self.session_start_price,
                                "session_high": self.session_high,
                                "session_low": self.session_low,
                                "session_move_pct": round(session_move_pct * 100, 2),
                                "median_daily_range": round(median_daily_range, 2),
                                "current_close": closes[-1]
                            }
                        )
        
        # Exit logic: if a position is open and it's approaching the exit hour
        if ctx.has_position() and current_hour >= self.exit_hour:
            return ctx.signal(
                "flat",
                confidence=0.9,
                metadata={
                    "reason": "time_based_exit_before_next_session",
                    "current_hour": current_hour,
                    "position_direction": ctx.position_direction(),
                    "entry_price": ctx.position["entry_price"]
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        # You could add adaptive logic here, e.g., adjust confidence based on recent performance
        # For now, just reset the cooldown.
        pass