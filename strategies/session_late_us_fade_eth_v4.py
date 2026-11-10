from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthUsSessionFade(Strategy):
    METADATA = {
        "name": "ETH US Session Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for ~1-2 hours hold, max 6 hours
        "warmup_bars": 50,
        "required_features": [], # No specific features needed, using OHLCV and market context
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fade_period = 20  # Lookback for session range and midpoint
        self.stdev_multiplier = 1.5 # How many standard deviations for "stretched"
        self.cooldown_bars = 6 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1
        self.min_session_range_bps = 50.0 # Minimum range for a valid fade setup
        self.max_crisis_score = 0.5 # Do not trade if crisis score is too high
        self.min_volatility_score = 0.3 # Require some volatility for a fade setup
        self.max_trend_confidence = 0.6 # Do not fade strong trends

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
        closes = ctx.closes(self.fade_period + 1)
        highs = ctx.highs(self.fade_period + 1)
        lows = ctx.lows(self.fade_period + 1)

        if len(closes) < self.fade_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Trading window: Late US session (UTC 18-24)
        # Entry only between 18:00 UTC and 23:00 UTC (to allow for exit before midnight)
        if not (18 <= current_hour < 23):
            return None
        
        # Only enter once per 24-hour period (session)
        # Check if we've already traded today (since midnight UTC)
        if ctx.bar_index > self.last_trade_bar:
             last_trade_timestamp = ctx.bar.timestamp - (ctx.bar_index - self.last_trade_bar) * ctx.bar.interval_seconds
             if last_trade_timestamp.day == ctx.bar.timestamp.day and 18 <= last_trade_timestamp.hour < 23:
                 return None # Already traded this session

        # Regime filters
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > self.max_crisis_score:
            return None # Avoid crisis regimes

        trend_regime_confidence = ctx.market.get("trend_regime_confidence", 0.0)
        if trend_regime_confidence > self.max_trend_confidence:
            return None # Avoid strong trending markets

        # Calculate session range and midpoint for the current trading window
        session_high = max(highs[-self.fade_period:])
        session_low = min(lows[-self.fade_period:])
        session_midpoint = (session_high + session_low) / 2.0
        session_range_bps = (session_high - session_low) / session_low * 10000

        if session_range_bps < self.min_session_range_bps:
            return None # Session not stretched enough to fade

        # Volatility check (using standard deviation of closes)
        volatility_std = self._stddev(closes, self.fade_period)
        if volatility_std is None or volatility_std / session_low * 10000 < self.min_volatility_score:
            return None # Not enough volatility

        # Determine if price is "stretched" from midpoint
        current_price = ctx.bar.close
        distance_from_midpoint = current_price - session_midpoint
        
        # Use a volatility-adjusted threshold for "stretched"
        # If price is outside session_midpoint +/- (stdev_multiplier * volatility_std)
        stretch_threshold = self.stdev_multiplier * volatility_std

        signal_direction = None
        confidence = 0.0
        reason = ""

        if distance_from_midpoint > stretch_threshold: # Price is significantly above midpoint, look for short
            if not ctx.has_position() or ctx.position_direction() == "long":
                signal_direction = "short"
                # Confidence scales with how far past the threshold we are
                confidence = min(1.0, (distance_from_midpoint - stretch_threshold) / (stretch_threshold * 0.5))
                reason = "us_session_fade_stretched_high"
        elif distance_from_midpoint < -stretch_threshold: # Price is significantly below midpoint, look for long
            if not ctx.has_position() or ctx.position_direction() == "short":
                signal_direction = "long"
                # Confidence scales with how far past the threshold we are
                confidence = min(1.0, (-distance_from_midpoint - stretch_threshold) / (stretch_threshold * 0.5))
                reason = "us_session_fade_stretched_low"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=max(0.3, confidence), # Ensure minimum confidence
                metadata={
                    "reason": reason,
                    "current_price": current_price,
                    "session_midpoint": session_midpoint,
                    "session_range_bps": session_range_bps,
                    "stretch_threshold": stretch_threshold,
                    "distance_from_midpoint": distance_from_midpoint,
                    "crisis_score": crisis_score,
                    "trend_confidence": trend_regime_confidence,
                    "bar_hour": current_hour
                }
            )

        # Exit logic: if we are in a position and it's near midnight UTC
        # Or if price crosses back to midpoint
        if ctx.has_position():
            if current_hour >= 23 and ctx.position_direction(): # Exit before midnight
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "session_close_time_exit",
                        "current_price": current_price,
                        "bar_hour": current_hour
                    }
                )
            
            # Additional exit condition: price returns to session midpoint
            if ctx.position_direction() == "long" and current_price >= session_midpoint:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "price_returned_to_midpoint_long_exit",
                        "current_price": current_price,
                        "session_midpoint": session_midpoint
                    }
                )
            elif ctx.position_direction() == "short" and current_price <= session_midpoint:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "price_returned_to_midpoint_short_exit",
                        "current_price": current_price,
                        "session_midpoint": session_midpoint
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic needed for this strategy
        pass