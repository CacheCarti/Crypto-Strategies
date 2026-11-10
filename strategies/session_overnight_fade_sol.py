from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolOvernightFade(Strategy):
    METADATA = {
        "name": "SolOvernightFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Max 2 hours, should exit before next session
        "warmup_bars": 50,
        "required_features": [], # No specific features needed for this core logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 0
        self.session_end_hour = 6    # UTC 6
        self.fade_threshold_pct = 0.8  # Percentage move to consider "stretched"
        self.cooldown_bars = 12      # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade

        # For tracking session data
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open = None
        self.session_bars_count = 0
        self.last_session_day = -1 # To detect new day/session

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Detect new session (new day or first bar of the session)
        is_new_session_day = current_day != self.last_session_day
        is_session_start_bar = current_hour == self.session_start_hour

        if is_new_session_day or (is_session_start_bar and self.session_bars_count == 0):
            # Reset session data at the start of a new session
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_open = ctx.bar.open
            self.session_bars_count = 1
            self.last_session_day = current_day
        else:
            # Update session high/low
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            if current_hour >= self.session_start_hour and current_hour < self.session_end_hour:
                self.session_bars_count += 1

        # Only consider trading within the session window
        if not (self.session_start_hour <= current_hour < self.session_end_hour):
            # If we have a position and it's outside the session, we should flat
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Do not trade if already in a position
        if ctx.has_position():
            return None

        # Don't trade in crisis regimes
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None

        # Calculate session midpoint and current price deviation
        if self.session_open is None or self.session_bars_count < 3: # Need some bars to form a session
            return None

        session_midpoint = (self.session_high + self.session_low) / 2.0
        session_range = self.session_high - self.session_low

        if session_range == 0:
            return None

        current_price = ctx.bar.close
        move_from_open_pct = (current_price - self.session_open) / self.session_open * 100.0
        
        # Check for "stretched" move
        stretch_factor = abs(move_from_open_pct) / self.fade_threshold_pct
        
        # Avoid fading strong trends (use a simple SMA as trend filter)
        sma_20 = self._sma(closes, 20)
        if sma_20 is None:
            return None
        
        # Fade Logic: If stretched up, look for short. If stretched down, look for long.
        signal_direction = None
        confidence = 0.0

        # Short condition
        if move_from_open_pct > self.fade_threshold_pct and \
           current_price > session_midpoint and \
           ctx.bar.close < ctx.bar.open: # Close lower than open (rejection)
            
            # Additional trend filter: only short if not strongly trending up
            if current_price < sma_20 * 1.005: # Price not significantly above SMA
                signal_direction = "short"
                confidence = min(0.7, 0.4 + stretch_factor * 0.3) # More stretched, more confidence

        # Long condition
        elif move_from_open_pct < -self.fade_threshold_pct and \
             current_price < session_midpoint and \
             ctx.bar.close > ctx.bar.open: # Close higher than open (rejection)
            
            # Additional trend filter: only long if not strongly trending down
            if current_price > sma_20 * 0.995: # Price not significantly below SMA
                signal_direction = "long"
                confidence = min(0.7, 0.4 + stretch_factor * 0.3) # More stretched, more confidence

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": f"session_overnight_fade_{signal_direction}",
                    "current_hour": current_hour,
                    "session_open": self.session_open,
                    "session_high": self.session_high,
                    "session_low": self.session_low,
                    "session_midpoint": session_midpoint,
                    "move_from_open_pct": move_from_open_pct,
                    "stretch_factor": stretch_factor,
                    "close": current_price,
                    "sma20": sma_20
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this simple fade strategy
        pass