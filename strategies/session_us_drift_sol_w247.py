from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionBreakout(Strategy):
    METADATA = {
        "name": "US Session Breakout (SOL)",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for ~1 hour to few hours hold
        "warmup_bars": 100,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 13  # UTC 13:00 (9 AM EST)
        self.session_end_hour = 21   # UTC 21:00 (5 PM EST)
        self.lookback_period = 20    # For median range calculation
        self.volatility_factor = 1.5 # How much the move should exceed median range
        self.max_move_exhaustion_factor = 2.5 # Avoid moves already too large
        self.min_signal_confidence = 0.6
        self.cooldown_bars = 5       # Bars to wait after a trade closes
        self.last_trade_bar_index = -self.cooldown_bars # Initialize to allow immediate trade

    def _median(self, data):
        """Calculates the median of a list of numbers."""
        if not data:
            return 0
        sorted_data = sorted(data)
        mid = len(sorted_data) // 2
        if len(sorted_data) % 2 == 0:
            return (sorted_data[mid - 1] + sorted_data[mid]) / 2
        else:
            return sorted_data[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1) # Need one more for range calculation
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        if len(closes) <= self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit any existing position before session end if it's still open
        if ctx.has_position() and current_hour == self.session_end_hour:
            # Only exit if the bar has just started at session_end_hour
            # To avoid exiting multiple times during the same hour if bars are shorter than 1 hour
            # (though this strategy is 1h bars, so this check is mostly for robustness)
            if ctx.bar.timestamp.minute == 0:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_end_exit",
                    "current_hour": current_hour,
                    "entry_price": ctx.position["entry_price"] if ctx.position else None,
                    "current_price": ctx.bar.close
                })

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar_index + self.cooldown_bars:
            return None

        # Logic for a new session entry
        if current_hour == self.session_start_hour and not ctx.has_position():
            # Check if we've already tried to trade this session
            # We'll use self.last_trade_bar_index to prevent multiple entries within the same session start hour
            # For 1-hour bars, checking current_hour is enough to prevent multiple entries per session.
            # We also need to ensure we only consider the *start* of the session for a new signal.
            # This check is implicitly handled by the `current_hour == self.session_start_hour` and `not ctx.has_position()`

            # Calculate recent true ranges to determine typical volatility
            true_ranges = []
            for i in range(1, self.lookback_period + 1):
                if i < len(highs) and i < len(lows) and i < len(closes):
                    tr = max(highs[-i] - lows[-i], abs(highs[-i] - closes[-i-1]), abs(lows[-i] - closes[-i-1]))
                    true_ranges.append(tr)
            
            if len(true_ranges) < self.lookback_period:
                return None
            
            median_true_range = self._median(true_ranges)
            
            # Analyze the last bar's movement (the one just before session start)
            # This is the bar that concluded the previous session/period, leading into the US session
            prev_close = closes[-2] # The close of the bar ending at ctx.bar.timestamp - 1 hour
            current_open = ctx.bar.open # The open of the current bar (start of US session)
            current_close = ctx.bar.close # The close of the current bar (end of 1st hour of US session)
            
            # The "decisive move" is the move from prev_close to current_close
            # This captures the initial reaction/momentum of the US session opening
            session_move = current_close - prev_close
            session_move_abs = abs(session_move)

            # Check for decisive move, but not exhausted (single-bar spike)
            min_decisive_move = median_true_range * self.volatility_factor
            max_exhausted_move = median_true_range * self.max_move_exhaustion_factor

            if session_move_abs > min_decisive_move and session_move_abs < max_exhausted_move:
                signal_direction = None
                reason = ""
                
                if session_move > 0: # Bullish move
                    signal_direction = "long"
                    reason = "us_session_bullish_breakout"
                elif session_move < 0: # Bearish move
                    signal_direction = "short"
                    reason = "us_session_bearish_breakout"

                if signal_direction:
                    confidence = min(0.9, 0.5 + (session_move_abs / (median_true_range * 4))) # Scale confidence by move size
                    
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        signal_direction,
                        confidence=confidence,
                        metadata={
                            "reason": reason,
                            "session_start_hour": self.session_start_hour,
                            "session_move_usd": session_move,
                            "median_true_range_usd": median_true_range,
                            "current_price": ctx.bar.close,
                            "previous_close": prev_close
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade index to ensure cooldown is applied after any closure
        self.last_trade_bar_index = ctx.bar_index
        # You could also use this to adapt parameters, e.g., if returns are consistently bad,
        # adjust volatility_factor or confidence thresholds.
        pass