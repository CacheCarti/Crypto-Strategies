from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionFade(Strategy):
    METADATA = {
        "name": "US Session Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Max 4 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "twitter_sentiment"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_open_hour = 13 # UTC
        self.session_close_hour = 21 # UTC
        self.session_mid_hour = 17 # UTC
        self.lookback_period = 20 # For EMA and volatility
        self.entry_threshold_multiplier = 1.5 # How many std devs away from midpoint for entry
        self.min_session_stretch_bars = 3 # Min bars into session before considering a fade
        self.trade_cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar_index = -self.trade_cooldown_bars

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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        is_us_session = self.session_open_hour <= current_hour < self.session_close_hour

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar_index + self.trade_cooldown_bars:
            if ctx.has_position():
                return ctx.signal("hold")
            return None

        # If already in a position, just hold.
        if ctx.has_position():
            # Exit near the end of the US session if still open
            if current_hour >= self.session_close_hour - 1: # Exit 1 hour before close
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "end_of_session_exit", "current_hour": current_hour})
            return ctx.signal("hold")

        # Only consider trades during the US session
        if not is_us_session:
            return None

        # Don't trade too early in the session
        bars_into_session = current_hour - self.session_open_hour
        if bars_into_session < self.min_session_stretch_bars:
            return None

        # Calculate session midpoint price
        # For simplicity, use the open of the session and current price to estimate a range.
        # A more complex version would track the VWAP or actual midpoint within the session.
        # Here, we'll use a simple moving average as a proxy for a "fair value" or midpoint.
        session_mid_price = self._ema(closes, self.lookback_period)
        if session_mid_price is None:
            return None

        current_price = ctx.bar.close
        price_stddev = self._stddev(closes, self.lookback_period)

        if price_stddev is None or price_stddev == 0:
            return None

        # Calculate deviation from session midpoint
        deviation = current_price - session_mid_price
        deviation_in_std = deviation / price_stddev

        # Regime filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        twitter_sentiment = ctx.features.get("twitter_sentiment", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Avoid trading in crisis regimes or very strong trends
        if regime in ["CRISIS", "MELTDOWN"] or ctx.market.get("crisis_score", 0.0) > 0.5:
            return None

        # Avoid fading strong trends
        if (trend_regime == "bull" and deviation_in_std > 0) or \
           (trend_regime == "bear" and deviation_in_std < 0):
            return None # Don't fade into a strong trend

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Fade an extended upward move
        if deviation_in_std > self.entry_threshold_multiplier:
            # Price is significantly above the session midpoint, consider shorting
            if current_hour >= self.session_mid_hour: # Only fade after session midpoint
                signal_direction = "short"
                confidence = min(0.8, (deviation_in_std - self.entry_threshold_multiplier) * 0.2 + 0.5)
                reason = "session_fade_short_extreme_up"

        # Fade an extended downward move
        elif deviation_in_std < -self.entry_threshold_multiplier:
            # Price is significantly below the session midpoint, consider longing
            if current_hour >= self.session_mid_hour: # Only fade after session midpoint
                signal_direction = "long"
                confidence = min(0.8, (-deviation_in_std - self.entry_threshold_multiplier) * 0.2 + 0.5)
                reason = "session_fade_long_extreme_down"

        if signal_direction:
            # Adjust confidence based on sentiment (e.g., less confident if sentiment is against the fade)
            if (signal_direction == "long" and twitter_sentiment < -0.2) or \
               (signal_direction == "short" and twitter_sentiment > 0.2):
                confidence *= 0.7 # Reduce confidence if sentiment is strongly against the fade direction

            # Further adjust confidence based on fear_greed_index (e.g., less confident in extreme greed/fear)
            if (signal_direction == "long" and fear_greed_index < 30) or \
               (signal_direction == "short" and fear_greed_index > 70):
                confidence *= 0.8 # Reduce confidence in extreme market sentiment

            if confidence > 0.5: # Only trade if confidence is sufficient
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "current_price": current_price,
                        "session_mid_price": session_mid_price,
                        "deviation_in_std": deviation_in_std,
                        "price_stddev": price_stddev,
                        "current_hour": current_hour,
                        "fear_greed_index": fear_greed_index,
                        "twitter_sentiment": twitter_sentiment,
                        "regime": regime,
                        "trend_regime": trend_regime
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust future sizing/parameters based on performance.
        pass