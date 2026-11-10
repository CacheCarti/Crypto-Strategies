from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionFade(Strategy):
    METADATA = {
        "name": "AsiaSessionFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Max hold until session end
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_start_hour = 0
        self.asia_end_hour = 8 # UTC 0-8 is 9 hours, so bar at 8:00 is last bar
        self.min_session_range_bps = 50.0  # Minimum range for a fade setup
        self.stretch_factor = 0.6  # How much of the session range needs to be covered to be 'stretched'
        self.cooldown_bars = 4 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # For tracking session data
        self.session_high: Optional[float] = None
        self.session_low: Optional[float] = None
        self.session_open: Optional[float] = None
        self.session_mid: Optional[float] = None
        self.session_active = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Session management
        if current_hour == self.asia_start_hour and not self.session_active:
            # New Asia session begins
            self.session_active = True
            self.session_open = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_mid = None # Will be calculated later
        elif self.session_active and current_hour != self.asia_start_hour:
            # Update session high/low
            if ctx.bar.high > self.session_high:
                self.session_high = ctx.bar.high
            if ctx.bar.low < self.session_low:
                self.session_low = ctx.bar.low
            self.session_mid = (self.session_high + self.session_low) / 2.0
        elif current_hour == self.asia_end_hour and self.session_active:
            # Session ending, reset for next day
            self.session_active = False
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        if not self.session_active or ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Only trade during Asia session, before it ends
        if not (self.asia_start_hour <= current_hour < self.asia_end_hour):
            return None

        # Do not trade if already in position
        if ctx.has_position():
            # If position is open near session end, consider closing
            if (self.asia_end_hour - current_hour <= 1): # Close if only 1 hour left
                 return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_proximity_exit", "current_hour": current_hour})
            return None

        # Strategy logic: Fade stretched moves towards session midpoint
        if self.session_open is None or self.session_high is None or self.session_low is None or self.session_mid is None:
            return None

        session_range = self.session_high - self.session_low
        if session_range == 0: return None
        session_range_bps = (session_range / self.session_open) * 10000

        if session_range_bps < self.min_session_range_bps:
            return None

        # Regime filters
        if ctx.market.get("trend_regime", "neutral") == "bull" and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
            # Do not fade strong bull trends
            return None
        if ctx.market.get("trend_regime", "neutral") == "bear" and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
            # Do not fade strong bear trends
            return None

        if ctx.market.get("crisis_score", 0.0) > 0.3 or ctx.regime == "crisis":
            return None # Avoid crisis regimes

        fear_greed = ctx.features.get("fear_greed_index", 50)
        if fear_greed < 25 or fear_greed > 75: # Avoid extreme sentiment
            return None

        # Check for stretched moves
        long_condition = False
        short_condition = False
        confidence = 0.6

        # Price significantly below session midpoint after a strong move down
        if current_price < self.session_mid and \
           (self.session_mid - current_price) / session_range > self.stretch_factor and \
           (current_price - self.session_low) / session_range < (1 - self.stretch_factor): # Not too close to low
            long_condition = True
            confidence += (self.session_mid - current_price) / session_range * 0.3 # Higher stretch, higher confidence

        # Price significantly above session midpoint after a strong move up
        if current_price > self.session_mid and \
           (current_price - self.session_mid) / session_range > self.stretch_factor and \
           (self.session_high - current_price) / session_range < (1 - self.stretch_factor): # Not too close to high
            short_condition = True
            confidence += (current_price - self.session_mid) / session_range * 0.3 # Higher stretch, higher confidence

        if long_condition:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(1.0, confidence),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=max(1, (self.asia_end_hour - current_hour)) * 3600, # Max hold until session end
                metadata={
                    "reason": "asia_session_fade_long",
                    "current_price": current_price,
                    "session_open": self.session_open,
                    "session_high": self.session_high,
                    "session_low": self.session_low,
                    "session_mid": self.session_mid,
                    "session_range_bps": session_range_bps,
                    "stretch_factor_used": self.stretch_factor,
                    "current_hour": current_hour,
                    "fear_greed": fear_greed,
                }
            )
        elif short_condition:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=min(1.0, confidence),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=max(1, (self.asia_end_hour - current_hour)) * 3600, # Max hold until session end
                metadata={
                    "reason": "asia_session_fade_short",
                    "current_price": current_price,
                    "session_open": self.session_open,
                    "session_high": self.session_high,
                    "session_low": self.session_low,
                    "session_mid": self.session_mid,
                    "session_range_bps": session_range_bps,
                    "stretch_factor_used": self.stretch_factor,
                    "current_hour": current_hour,
                    "fear_greed": fear_greed,
                }
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally adjust state or log
        pass