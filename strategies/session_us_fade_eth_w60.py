from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionFadeStrategy(Strategy):
    METADATA = {
        "name": "US Session Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2, # Up to 2 hours, but often shorter
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_midpoint_period = 20 # Bars for session midpoint calculation
        self.stdev_period = 20 # For detecting stretched moves
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade
        self.traded_today = False # Track if we traded in the current UTC day

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

        # Reset traded_today flag at the start of a new UTC day
        if current_hour == 0 and not self.traded_today: # Only reset once at 00:00 UTC
            self.traded_today = False
        elif current_hour == 1: # After 00:00 UTC, we can set it to false for the new day
             self.traded_today = False

        # US Session is UTC 13-21
        is_us_session = 13 <= current_hour < 21
        
        # Exit any open position if we're past the session end, or if it's the last bar of the session (20:00 UTC)
        if ctx.has_position() and current_hour >= 20: # Exit at 20:00 UTC bar, before 21:00 UTC
            self.traded_today = True # Mark that we traded today to prevent re-entry
            return ctx.signal("flat", confidence=0.8, metadata={
                "reason": "session_end_exit",
                "current_hour": current_hour,
                "close_price": ctx.bar.close
            })

        # Do not enter if not in US session, or if already traded today, or in cooldown
        if not is_us_session or self.traded_today or (ctx.bar_index - self.last_trade_bar < self.cooldown_bars):
            return None

        # Do not enter if already in a position
        if ctx.has_position():
            return None

        # Get relevant data for indicators
        recent_closes = ctx.closes(self.session_midpoint_period + self.stdev_period)
        if len(recent_closes) < self.session_midpoint_period + self.stdev_period:
            return None

        # Calculate session midpoint (SMA of last N bars in the session)
        session_midpoint = self._sma(recent_closes[-self.session_midpoint_period:], self.session_midpoint_period)
        if session_midpoint is None:
            return None

        # Calculate standard deviation of recent prices to detect stretched moves
        price_stddev = self._stddev(recent_closes[-self.stdev_period:], self.stdev_period)
        if price_stddev is None or price_stddev == 0:
            return None

        current_price = ctx.bar.close

        # Conditions for fading a stretched move
        # We look for price to be significantly away from the session midpoint
        # and not in a strong trend or crisis regime
        
        # Regime filters
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Avoid trading in crisis or strong trends
        if crisis_score > 0.5 or trend_regime != "neutral":
            return None

        # Fading logic: price is above midpoint by > 2 stddevs -> consider short
        # Price is below midpoint by > 2 stddevs -> consider long
        
        # Long condition: price is stretched significantly below midpoint
        if current_price < session_midpoint - (2.0 * price_stddev):
            self.last_trade_bar = ctx.bar_index
            self.traded_today = True
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "session_fade_long_stretched_down",
                    "current_price": current_price,
                    "session_midpoint": session_midpoint,
                    "price_stddev": price_stddev,
                    "regime": market_regime,
                    "trend_regime": trend_regime,
                    "crisis_score": crisis_score
                }
            )

        # Short condition: price is stretched significantly above midpoint
        elif current_price > session_midpoint + (2.0 * price_stddev):
            self.last_trade_bar = ctx.bar_index
            self.traded_today = True
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "session_fade_short_stretched_up",
                    "current_price": current_price,
                    "session_midpoint": session_midpoint,
                    "price_stddev": price_stddev,
                    "regime": market_regime,
                    "trend_regime": trend_regime,
                    "crisis_score": crisis_score
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis or adaptive logic (not implemented here)
        # For this strategy, we already set traded_today=True on entry to prevent multiple entries within the same session.
        pass