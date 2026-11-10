from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightSessionBreakout(Strategy):
    METADATA = {
        "name": "OvernightSessionBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Approx 2 hours, allowing for session close
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_session_start_hour = 0  # UTC 00:00
        self.overnight_session_end_hour = 6    # UTC 06:00 (exclusive, so 05:00 bar close)
        self.entry_cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.entry_cooldown_bars * 2 # Initialize to allow first trade
        self.min_session_range_multiplier = 1.5 # Session move must exceed 1.5x median range
        self.median_range_period = 30 # Bars to calculate median range
        self.max_single_bar_spike_ratio = 0.6 # Max fraction of session move from a single bar
        self.exit_hour = 12 # Exit before US session (UTC 12:00)

    def _median(self, data):
        if not data:
            return 0.0
        sorted_data = sorted(data)
        mid = len(sorted_data) // 2
        if len(sorted_data) % 2 == 0:
            return (sorted_data[mid - 1] + sorted_data[mid]) / 2.0
        return sorted_data[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_range_period + 1)
        highs = ctx.highs(self.median_range_period + 1)
        lows = ctx.lows(self.median_range_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: If we have a position and it's time to exit before the next session
        if ctx.has_position() and current_hour == self.exit_hour:
            position_direction = ctx.position_direction()
            exit_reason = f"pre_us_session_exit_{position_direction}"
            return ctx.signal("flat", confidence=0.7, metadata={"reason": exit_reason, "current_hour": current_hour})

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.entry_cooldown_bars:
            return None

        # --- Entry Logic ---
        # Look for entry at the beginning of the next session (after overnight)
        # We trigger on the bar *after* the overnight session ends, i.e., at 06:00 UTC bar open (07:00 current_hour)
        if current_hour == self.overnight_session_end_hour: # This bar is the first bar AFTER the overnight session
            # Need overnight session bars. From 00:00 to 05:00 (6 bars total)
            # ctx.closes(-1) is current bar, ctx.closes(-2) is previous bar.
            # We need bars from current_hour-1 (05:00) down to current_hour-6 (00:00)
            
            # Check if we have enough bars to cover the overnight session
            # We need 6 bars for the session (00, 01, 02, 03, 04, 05 UTC)
            # The current bar is 06:00 UTC. So we need the 6 bars before it.
            required_history_for_session = 6 
            if len(closes) < required_history_for_session + 1: # +1 for current bar
                return None

            session_low = float('inf')
            session_high = float('-inf')
            session_open = 0.0
            session_close = 0.0
            
            # Extract overnight session bars (00:00 to 05:00 UTC)
            # ctx.lows() and ctx.highs() are ordered oldest first
            # The last 6 bars are the overnight session if current_hour is 06.
            # No, this is tricky. ctx.bar.timestamp refers to the current bar.
            # We need to look back from the current bar (06:00) to find the 00:00-05:00 session.
            
            # Let's adjust to look at the bars *ending* the session.
            # If current_hour is 06, we are looking at the bar for 06:00-07:00.
            # The previous bar was 05:00-06:00.
            # The session bars are index -1 (05:00), -2 (04:00), ..., -6 (00:00).
            
            overnight_highs = highs[-required_history_for_session-1:-1] # 00:00 to 05:00
            overnight_lows = lows[-required_history_for_session-1:-1]
            overnight_closes = closes[-required_history_for_session-1:-1]
            overnight_opens = ctx.opens(required_history_for_session+1)[-required_history_for_session-1:-1]

            if len(overnight_highs) < required_history_for_session:
                return None # Not enough history for the session

            session_open = overnight_opens[0] # Open of 00:00 bar
            session_close = overnight_closes[-1] # Close of 05:00 bar
            session_low = min(overnight_lows)
            session_high = max(overnight_highs)

            session_range = session_high - session_low
            session_move = session_close - session_open

            # Calculate median true range for comparison
            ranges = []
            for i in range(1, self.median_range_period + 1):
                if len(highs) > i and len(lows) > i:
                    ranges.append(highs[-i] - lows[-i])
            median_range = self._median(ranges)

            # Check if session move is decisive
            if abs(session_move) < median_range * self.min_session_range_multiplier:
                return None # Not a decisive move

            # Check for single-bar spikes
            max_bar_move = 0.0
            for i in range(required_history_for_session):
                max_bar_move = max(max_bar_move, abs(overnight_closes[i] - overnight_opens[i]))

            if max_bar_move > abs(session_move) * self.max_single_bar_spike_ratio:
                return None # Session move dominated by a single spike

            signal_direction = None
            if session_move > 0: # Bullish session
                signal_direction = "long"
            elif session_move < 0: # Bearish session
                signal_direction = "short"

            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=0.8,
                    metadata={
                        "reason": f"overnight_session_breakout_{signal_direction}",
                        "session_move": session_move,
                        "session_range": session_range,
                        "median_range": median_range,
                        "session_open": session_open,
                        "session_close": session_close,
                        "current_price": ctx.bar.close
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close or adapt state
        pass