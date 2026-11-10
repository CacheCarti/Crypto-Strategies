from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthLateUSDrift(Strategy):
    METADATA = {
        "name": "EthLateUSDrift",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": [], # No external features required for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24  # For median range calculation (1 day)
        self.entry_cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_signal_bar_index = -self.entry_cooldown_bars # Initialize to allow immediate trade
        self.session_start_hour = 18 # UTC 18:00 (6 PM UTC)
        self.session_end_hour = 24   # UTC 24:00 (Midnight UTC)
        self.min_move_ratio = 0.5    # Minimum move as a ratio of median range
        self.max_move_ratio = 2.0    # Max move as a ratio of median range (avoid exhaustion)
        self.min_volume_ratio = 1.2  # Volume confirmation for the move

    def _median_true_range(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        true_ranges = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            true_ranges.append(tr)
        
        # Calculate median of the last 'period' true ranges
        if len(true_ranges) < period:
            return None
        
        sorted_trs = sorted(true_ranges[-period:])
        mid = len(sorted_trs) // 2
        if len(sorted_trs) % 2 == 0:
            return (sorted_trs[mid - 1] + sorted_trs[mid]) / 2
        else:
            return sorted_trs[mid]

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)
        volumes = ctx.volumes(self.lookback_period + 1)

        if len(closes) < self.lookback_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown period after a previous signal
        if ctx.bar_index - self.last_signal_bar_index < self.entry_cooldown_bars:
            return None

        # Exit existing positions before midnight UTC if it's not a new entry
        if ctx.has_position() and current_hour == 0: # 00:00 UTC
            # Ensure we only exit if the position was entered in a prior session
            # This prevents exiting a position that was just entered at 23:00 and held for 1 bar
            if (ctx.bar_index - self.last_signal_bar_index) > (self.session_end_hour - self.session_start_hour):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour, "price": ctx.bar.close})

        # Entry logic: Only consider entering during the late US session (UTC 18-24)
        if self.session_start_hour <= current_hour < self.session_end_hour:
            # Calculate median true range for volatility normalization
            median_tr = self._median_true_range(highs, lows, closes, self.lookback_period)
            if median_tr is None or median_tr == 0:
                return None

            # Calculate session move relative to session start
            # Find the closest bar to the session start hour within the current day
            session_open_price = None
            session_volume = 0
            for i in range(self.lookback_period, 0, -1): # Iterate backwards from current bar
                if ctx.bar.timestamp.hour >= self.session_start_hour and (ctx.bar.timestamp.hour - i) < self.session_start_hour:
                    # This implies current bar is in the session, and i bars ago was before session start
                    # Or, more simply, find the bar that corresponds to the start of the session for today
                    # For simplicity, let's just use the close from self.session_start_hour
                    # This strategy works hourly, so we need to find the close at the session_start_hour
                    # This assumes the current bar is the first bar of the new session period
                    # A more robust approach would track session open explicitly
                    pass # This is tricky with fixed lookback, simplifying to last full hour's close
            
            # Simplified session start: use the close at the *start* of the current session hour block
            # For a strategy that fires at 18:00, the "session_open" would be the close at 17:00
            # For a strategy that fires at 19:00, the "session_open" would be the close at 17:00
            # Let's define session_open_price as the close at self.session_start_hour
            
            # We need to find the close at the session_start_hour for the current day
            # This requires looking back in `closes` until we hit the session_start_hour
            # Given we are on hourly bars, if current_hour is 18, the previous bar is 17.
            # If current_hour is 19, the previous bar is 18 etc.
            
            # Let's approximate the session open by taking the close from the bar that *started* this session.
            # If current_hour is 18, we need closes[-1] (the 17:00 bar).
            # If current_hour is 19, we need closes[-2] (the 17:00 bar).
            # The index for the session_start_hour bar would be (current_hour - self.session_start_hour + 1) bars ago.
            
            # Ensure we have enough bars for the session open price
            session_open_idx = -(current_hour - self.session_start_hour + 1)
            if abs(session_open_idx) >= len(closes):
                return None # Not enough history to determine session open

            session_open_price = closes[session_open_idx]
            
            # Calculate the price change since the session open
            session_price_change = ctx.bar.close - session_open_price
            
            # Calculate the volume during the session so far
            # Sum volumes from session_open_idx up to the current bar (excluding current bar's volume for now)
            session_volume_sum = sum(volumes[session_open_idx:])
            
            # Calculate average volume for comparison
            avg_volume = self._sma(volumes, self.lookback_period)
            if avg_volume is None or avg_volume == 0:
                return None
            
            # Conditions for entry:
            # 1. Decisive move: price change exceeds min_move_ratio * median_tr
            # 2. Not exhausted: price change does not exceed max_move_ratio * median_tr
            # 3. Volume confirmation: current bar's volume is above average
            # 4. Not already in position
            
            move_magnitude = abs(session_price_change)
            
            # Check for single-bar spikes by comparing current bar's move to session move
            current_bar_move = abs(ctx.bar.close - closes[-1])
            if current_bar_move > (move_magnitude * 0.8): # If current bar is most of the move, it's a spike
                return None

            if (move_magnitude > (self.min_move_ratio * median_tr) and
                move_magnitude < (self.max_move_ratio * median_tr) and
                ctx.bar.volume > (self.min_volume_ratio * avg_volume) and
                not ctx.has_position()):

                confidence = 0.6 + (move_magnitude / (self.max_move_ratio * median_tr)) * 0.3 # Scale confidence

                if session_price_change > 0: # Bullish drift
                    self.last_signal_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        metadata={
                            "reason": "late_us_session_bull_drift",
                            "session_start_hour": self.session_start_hour,
                            "current_hour": current_hour,
                            "session_open_price": session_open_price,
                            "current_close": ctx.bar.close,
                            "session_price_change": session_price_change,
                            "median_true_range": median_tr,
                            "volume": ctx.bar.volume,
                            "avg_volume": avg_volume
                        }
                    )
                elif session_price_change < 0: # Bearish drift
                    self.last_signal_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=confidence,
                        metadata={
                            "reason": "late_us_session_bear_drift",
                            "session_start_hour": self.session_start_hour,
                            "current_hour": current_hour,
                            "session_open_price": session_open_price,
                            "current_close": ctx.bar.close,
                            "session_price_change": session_price_change,
                            "median_true_range": median_tr,
                            "volume": ctx.bar.volume,
                            "avg_volume": avg_volume
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last signal bar index to allow new entries after cooldown,
        # or use this for adaptive sizing / logging.
        # For this strategy, the cooldown is managed in on_bar.
        pass