from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionDriftBTC(Strategy):
    METADATA = {
        "name": "SessionDriftBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this hypothesis
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20 # For median range calculation
        self.session_start_hour_utc = 13
        self.session_end_hour_utc = 21 # One hour before close, to exit
        self.min_session_move_factor = 1.5 # Multiplier of median range
        self.last_signal_bar_index = -self.METADATA["warmup_bars"] - 1 # Ensure no signal at start
        self.cooldown_bars = 6 # Cooldown period after a trade

    def _median_true_range(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        
        # Calculate median of the most recent 'period' true ranges
        sorted_trs = sorted(trs[-period:])
        mid = len(sorted_trs) // 2
        if len(sorted_trs) % 2 == 0:
            return (sorted_trs[mid - 1] + sorted_trs[mid]) / 2
        else:
            return sorted_trs[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # Exit existing position before session end if still open
        if ctx.has_position() and current_hour == self.session_end_hour_utc:
            position_direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]
            exit_price = ctx.bar.close
            return_bps = (exit_price - entry_price) / entry_price * 10000 if position_direction == "long" else (entry_price - exit_price) / entry_price * 10000
            
            self.last_signal_bar_index = ctx.bar_index # Reset cooldown
            return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour, "position_direction": position_direction, "return_bps": return_bps})

        # Entry logic: Check for decisive session move
        # We look for the move *after* the session has started, so the current bar is part of the session
        if current_hour == self.session_start_hour_utc + 1: # One hour into the session
            # Calculate median range
            median_tr = self._median_true_range(highs[:-1], lows[:-1], closes[:-1], self.lookback_period)
            if median_tr is None:
                return None
            
            # Get previous bar's close as session start reference
            session_start_close = closes[-2]
            current_close = ctx.bar.close

            move_abs = abs(current_close - session_start_close)
            min_decisive_move = median_tr * self.min_session_move_factor

            if move_abs >= min_decisive_move:
                # Check for single-bar spike (current bar's range vs. move)
                current_bar_range = ctx.bar.high - ctx.bar.low
                if current_bar_range > (move_abs * 1.5): # If current bar accounts for too much of the move, it might be a spike
                    return None # Skip potential spike

                signal_direction = None
                if current_close > session_start_close: # Upward move
                    signal_direction = "long"
                elif current_close < session_start_close: # Downward move
                    signal_direction = "short"

                if signal_direction and not ctx.has_position():
                    self.last_signal_bar_index = ctx.bar_index
                    return ctx.signal(
                        signal_direction,
                        confidence=0.7,
                        metadata={
                            "reason": "decisive_session_drift",
                            "session_start_hour": self.session_start_hour_utc,
                            "current_hour": current_hour,
                            "move_abs": move_abs,
                            "min_decisive_move": min_decisive_move,
                            "median_tr": median_tr,
                            "session_start_close": session_start_close,
                            "current_close": current_close,
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after any position close
        self.last_signal_bar_index = ctx.bar_index
        # Optional: Log for analysis or adapt strategy parameters
        # ctx.log_info(f"Position closed: {position['direction']} return {position['return_bps']} bps, held for {position['bars_held']} bars.")
        pass