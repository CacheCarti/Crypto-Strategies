from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthSessionHandoff(Strategy):
    METADATA = {
        "name": "EthSessionHandoff",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours, crossing session
        "warmup_bars": 30,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 18  # UTC 18:00 (late US session)
        self.entry_hour_end = 23    # UTC 23:00 (end of late US session for entry)
        self.exit_hour = 0          # UTC 00:00 (start of Asia session)
        self.cooldown_bars = 4      # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade

        # Parameters for regime gating
        self.trend_period = 20
        self.volatility_period = 20
        self.min_trend_confidence = 0.6
        self.max_volatility_crisis_score = 0.5 # Avoid trading in very high crisis

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        
        # --- Regime Gating ---
        # Only trade if market trend is not strong bear and not in crisis
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        if trend_regime == "bear" and trend_confidence > self.min_trend_confidence:
            return None # Stand down in strong bear trends
        if crisis_score > self.max_volatility_crisis_score:
            return None # Stand down in high crisis environments

        # --- Exit Logic ---
        if ctx.has_position():
            # Mandatory exit at the session boundary
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_boundary_exit",
                    "exit_hour": self.exit_hour,
                    "current_hour": current_hour
                })
            return None # Hold position until exit condition or stop/profit

        # --- Entry Logic ---
        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Only consider entries within the defined entry window
        if not (self.entry_hour_start <= current_hour <= self.entry_hour_end):
            return None

        # Check if we've already tried to enter this "trading day" (from previous exit_hour to current)
        # This prevents multiple entries within the same session window
        # We define a "trading day" as starting from self.exit_hour
        if self.last_trade_bar != -self.cooldown_bars - 1: # If a trade has ever happened
            last_trade_timestamp = ctx.closes(ctx.bar_index - self.last_trade_bar + 1)[0] # This is hacky, need real timestamp
            # A more robust way is to store the actual timestamp of last trade or use ctx.bar.timestamp.day
            # For simplicity, we'll use a daily reset based on exit_hour
            if hasattr(self, '_last_entry_day') and self._last_entry_day == ctx.bar.timestamp.day:
                return None # Already entered today

        # Calculate session impulse
        # We need data from the start of the current session or at least a few hours back
        # Let's define the session start as current_hour - 6 for impulse calculation
        # This is a simplification. A more robust way would be to track the true session open.
        
        # We need enough bars to calculate the session's direction.
        # Let's look at the price change over the last 3-6 hours within the session window.
        impulse_period = 3 # Look back 3 hours for impulse
        if len(closes) < impulse_period:
            return None
        
        # Calculate the price change over the impulse_period
        # The 'closes' array is oldest first, so closes[-1] is current, closes[-impulse_period-1] is the start
        start_price = closes[-impulse_period - 1]
        current_price = closes[-1]
        price_change_pct = (current_price - start_price) / start_price * 100

        # Define a threshold for significant impulse
        impulse_threshold_pct = 0.25 # 0.25% move

        signal = None
        confidence = 0.6
        direction = None
        reason = ""

        if price_change_pct > impulse_threshold_pct:
            direction = "long"
            reason = "late_us_session_bullish_impulse"
        elif price_change_pct < -impulse_threshold_pct:
            direction = "short"
            reason = "late_us_session_bearish_impulse"
        
        if direction:
            self.last_trade_bar = ctx.bar_index
            self._last_entry_day = ctx.bar.timestamp.day # Store day to prevent re-entry
            return ctx.signal(direction, confidence=confidence, metadata={
                "reason": reason,
                "current_hour": current_hour,
                "impulse_period": impulse_period,
                "price_change_pct": price_change_pct,
                "impulse_threshold_pct": impulse_threshold_pct,
                "trend_regime": trend_regime,
                "crisis_score": crisis_score
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for debugging or adaptive sizing
        pass