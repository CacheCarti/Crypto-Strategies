from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionAsiaFadeSOL(Strategy):
    METADATA = {
        "name": "Session Asia Fade SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,       # Stop loss in bps
        "declared_tp_bps": 400.0,       # Take profit in bps
        "declared_hold_seconds": 3600 * 2,  # Intended avg hold time (2 hours for a fade)
        "warmup_bars": 50,              # Bars needed for indicators
        "required_features": ["fear_greed_index"], # Using fear_greed_index as a regime filter
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_midpoint_period = 8  # Asia session is 0-8 UTC, so 8 bars for 1h chart
        self.stretch_threshold = 0.005 # 0.5% stretch from midpoint
        self.max_stretch_factor = 0.015 # 1.5% max stretch to avoid fading extreme moves
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trade

        # Store session data
        self.current_session_open_bar_index = -1
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_closes = []

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.session_midpoint_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None # Not enough data for warmup

        current_hour = ctx.bar.timestamp.hour

        # Reset session data at the start of a new Asia session (UTC 0)
        if current_hour == 0 and ctx.bar_index != self.current_session_open_bar_index:
            self.current_session_open_bar_index = ctx.bar_index
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_closes = [ctx.bar.close]
            return None # Wait for session to develop

        # Update session data
        if current_hour >= 0 and current_hour < self.session_midpoint_period:
            if ctx.bar_index > self.current_session_open_bar_index: # Ensure not to re-add on the same bar
                self.session_high = max(self.session_high, ctx.bar.high)
                self.session_low = min(self.session_low, ctx.bar.low)
                self.session_closes.append(ctx.bar.close)

        # Only consider trading during Asia session (UTC 0-8)
        if not (current_hour >= 0 and current_hour < self.session_midpoint_period):
            if ctx.has_position() and ctx.position_direction() in ["long", "short"]:
                # Exit any open position if we are outside the Asia session
                # This acts as a time-based stop for positions held too long
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Don't trade if already in a position
        if ctx.has_position():
            return None

        # Regime filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        if fear_greed_index < 20 or fear_greed_index > 80: # Avoid extreme fear/greed
            return None
        
        # Avoid trading in crisis regimes
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN", "HIGH_VOL"]:
            return None

        # Ensure enough session bars have passed to calculate a meaningful midpoint
        if len(self.session_closes) < 3: # Need at least 3 bars to form a session range
            return None

        # Calculate session midpoint (simple average of session high/low for now, or SMA of session closes)
        session_midpoint = (max(self.session_closes) + min(self.session_closes)) / 2.0
        # Alternatively, use SMA of session closes for a smoother midpoint
        # session_midpoint = self._sma(self.session_closes, len(self.session_closes))
        # if session_midpoint is None:
        #     return None

        current_price = ctx.bar.close

        # Calculate stretch from midpoint
        stretch_bps = (current_price - session_midpoint) / session_midpoint * 10000

        # Long entry: Price stretched significantly below midpoint, but not too far
        if stretch_bps < -self.stretch_threshold * 10000 and stretch_bps > -self.max_stretch_factor * 10000:
            # Check for recent bounce confirmation (e.g., current close > previous close)
            if ctx.bar.close > ctx.opens(1)[0]: # Current bar is green
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.65,
                    metadata={
                        "reason": "asia_session_fade_long",
                        "session_midpoint": session_midpoint,
                        "current_price": current_price,
                        "stretch_bps": stretch_bps,
                        "fear_greed": fear_greed_index,
                    }
                )

        # Short entry: Price stretched significantly above midpoint, but not too far
        elif stretch_bps > self.stretch_threshold * 10000 and stretch_bps < self.max_stretch_factor * 10000:
            # Check for recent rejection confirmation (e.g., current close < previous close)
            if ctx.bar.close < ctx.opens(1)[0]: # Current bar is red
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.65,
                    metadata={
                        "reason": "asia_session_fade_short",
                        "session_midpoint": session_midpoint,
                        "current_price": current_price,
                        "stretch_bps": stretch_bps,
                        "fear_greed": fear_greed_index,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Adaptive logic based on trade outcome
        # For this strategy, we just log for now
        ctx.log(f"Position closed: {position['direction']} PnL: {position['return_bps']:.2f} bps, Held: {position['bars_held']} bars")