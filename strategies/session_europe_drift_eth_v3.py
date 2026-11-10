from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeSessionDrift(Strategy):
    METADATA = {
        "name": "EuropeSessionDrift",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Max hold until end of session (12 UTC) or stop
        "warmup_bars": 30,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7 AM
        self.entry_hour_end = 10   # UTC 10 AM (allow entry up to this hour)
        self.exit_hour = 12        # UTC 12 PM (exit by this hour)
        self.min_session_move_factor = 0.003 # 0.3% move from session open
        self.median_range_period = 20 # For median true range
        self.cooldown_bars = 5     # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade

        self.last_session_open_price = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.traded_this_session = False
        self.session_start_bar_index = -1
        self.last_signal_direction = None # To detect transitions

    def _median(self, values):
        if not values:
            return 0
        sorted_values = sorted(values)
        mid = len(sorted_values) // 2
        if len(sorted_values) % 2 == 0:
            return (sorted_values[mid - 1] + sorted_values[mid]) / 2
        else:
            return sorted_values[mid]

    def _true_range(self, high, low, prev_close):
        return max(high - low, abs(high - prev_close), abs(low - prev_close))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_range_period + 1)
        highs = ctx.highs(self.median_range_period + 1)
        lows = ctx.lows(self.median_range_period + 1)
        opens = ctx.opens(self.median_range_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset session state at the start of a new Europe session
        if current_hour == self.entry_hour_start and ctx.bar_index != self.session_start_bar_index:
            self.last_session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.traded_this_session = False
            self.session_start_bar_index = ctx.bar_index
        elif current_hour > self.entry_hour_start and self.session_start_bar_index == -1:
            # If strategy starts mid-session, initialize session open price
            # This is a fallback and might not be precise, but better than None
            self.last_session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.traded_this_session = False
            self.session_start_bar_index = ctx.bar_index # Mark as started for this bar
        elif self.last_session_open_price is None and current_hour < self.entry_hour_start:
            # If we are before the session starts, ensure session open is None
            self.last_session_open_price = None
            self.traded_this_session = False
            self.session_start_bar_index = -1

        # Update session high/low if within session hours (or just after)
        if self.last_session_open_price is not None:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Exit logic
        if ctx.has_position():
            if current_hour >= self.exit_hour:
                reason = "session_end_exit"
                if ctx.position_direction() == "long":
                    reason += "_long"
                else:
                    reason += "_short"
                return ctx.signal("flat", confidence=0.7, metadata={"reason": reason, "exit_hour": current_hour, "bar_index": ctx.bar_index})

        # Entry logic
        if not ctx.has_position() and not self.traded_this_session and \
           current_hour >= self.entry_hour_start and current_hour <= self.entry_hour_end and \
           (ctx.bar_index - self.last_trade_bar) > self.cooldown_bars and \
           self.last_session_open_price is not None:

            # Calculate median true range for volatility filter
            if len(closes) < self.median_range_period + 1:
                return None
            
            true_ranges = []
            for i in range(1, self.median_range_period + 1):
                tr = self._true_range(highs[i], lows[i], closes[i-1])
                true_ranges.append(tr)
            median_tr = self._median(true_ranges)
            
            # Skip if median_tr is zero or very small to avoid division by zero or tiny moves
            if median_tr == 0:
                return None

            # Calculate session move
            session_move_pct = (ctx.bar.close - self.last_session_open_price) / self.last_session_open_price
            
            # Check for decisive move
            min_move_threshold = self.min_session_move_factor * (median_tr / ctx.bar.close) # Scale threshold by ATR
            
            # Check for single-bar spike (current bar's move is too large relative to session move)
            bar_move_pct = (ctx.bar.close - ctx.bar.open) / ctx.bar.open
            
            # Long condition
            if session_move_pct > min_move_threshold and \
               abs(bar_move_pct) < abs(session_move_pct) * 0.8: # Bar move is not almost the entire session move
                
                # Check for transition: only signal if we weren't just thinking short, or if this is the first signal
                if self.last_signal_direction != "long":
                    self.traded_this_session = True
                    self.last_trade_bar = ctx.bar_index
                    self.last_signal_direction = "long"
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "europe_session_drift_long",
                            "session_move_pct": session_move_pct,
                            "median_tr": median_tr,
                            "current_hour": current_hour,
                            "session_open": self.last_session_open_price,
                            "close": ctx.bar.close
                        }
                    )
            # Short condition
            elif session_move_pct < -min_move_threshold and \
                 abs(bar_move_pct) < abs(session_move_pct) * 0.8: # Bar move is not almost the entire session move

                # Check for transition
                if self.last_signal_direction != "short":
                    self.traded_this_session = True
                    self.last_trade_bar = ctx.bar_index
                    self.last_signal_direction = "short"
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "europe_session_drift_short",
                            "session_move_pct": session_move_pct,
                            "median_tr": median_tr,
                            "current_hour": current_hour,
                            "session_open": self.last_session_open_price,
                            "close": ctx.bar.close
                        }
                    )
        
        # Reset last_signal_direction if no signal was generated this bar, to allow new signals later
        # This prevents repeated signals if conditions stay true, but allows a flip if direction changes
        if not ctx.has_position() and (ctx.bar_index - self.last_trade_bar) > 1: # Only reset if not just traded
             self.last_signal_direction = None

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any state that should be reset after a position closes if needed
        # For this strategy, traded_this_session handles daily limits
        # Cooldown is handled by last_trade_bar
        pass