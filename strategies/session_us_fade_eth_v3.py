from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionFade(Strategy):
    METADATA = {
        "name": "US Session Fade ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2, # Up to 2 hours, but often shorter
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_open_hour = 13  # UTC
        self.session_close_hour = 21 # UTC
        self.lookback_period = 20    # For volatility and midpoint
        self.max_stretch_factor = 2.0 # How many ATRs away to consider stretched
        self.min_stretch_factor = 1.0 # Minimum stretch to consider fading
        self.cooldown_bars = 5       # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow first trade

        # State to track for session logic
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open_price = None
        self.session_start_bar_index = -1
        self.traded_this_session = False

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)
        volumes = ctx.volumes(self.lookback_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Check for new session start
        is_session_start_bar = False
        if current_hour == self.session_open_hour and (ctx.bar_index == 0 or ctx.bar.timestamp.hour != ctx.closes(2)[0].hour):
            # This is the first bar of the US session
            self.session_open_price = current_price
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
            self.traded_this_session = False
            is_session_start_bar = True
        elif current_hour == self.session_open_hour and self.session_start_bar_index == -1:
            # Handle cases where strategy starts mid-session
            self.session_open_price = ctx.opens(1)[0]
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
            self.traded_this_session = False
            is_session_start_bar = True

        # Update session high/low if within session
        if self.session_open_price is not None and self.session_open_hour <= current_hour < self.session_close_hour:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
        else:
            # Reset session data if outside session hours
            self.session_open_price = None
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_start_bar_index = -1
            self.traded_this_session = False

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit existing position if close to session end or if it's past session end
        if ctx.has_position():
            if current_hour >= self.session_close_hour -1: # Exit 1 hour before close
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            # If position is held past session close, flat it immediately
            if current_hour >= self.session_close_hour and ctx.position_direction() is not None:
                return ctx.signal("flat", confidence=0.9, metadata={"reason": "forced_session_close_exit", "current_hour": current_hour})


        # Only consider entries within the US session, and not in the first bar of the session
        if not (self.session_open_hour <= current_hour < self.session_close_hour) or is_session_start_bar:
            return None

        # Don't trade if already traded this session
        if self.traded_this_session:
            return None

        # Calculate indicators
        atr = self._atr(highs, lows, closes, self.lookback_period)
        sma = self._sma(closes, self.lookback_period)

        if atr is None or sma is None:
            return None

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Do not fade strong trends or crisis regimes
        if regime in ["CRISIS", "MELTDOWN"] or trend_regime in ["bull", "bear"] or fear_greed_index < 30:
            return None

        # Session midpoint
        if self.session_open_price is None:
            return None # Should not happen if within session boundaries

        session_midpoint = (self.session_high + self.session_low) / 2.0
        # If the session has barely moved, the midpoint is unreliable or not relevant
        if abs(self.session_high - self.session_low) < atr * 0.5: # Session too narrow
            return None

        # Calculate current stretch from session open
        current_stretch_up = current_price - self.session_open_price
        current_stretch_down = self.session_open_price - current_price

        signal_direction = None
        confidence = 0.0
        reason = "no_signal"

        # Long fade: Price is significantly below session open, and we expect a reversion towards the midpoint
        # Only if price is below midpoint, and stretched downwards
        if current_price < session_midpoint:
            stretch_from_midpoint = session_midpoint - current_price
            if stretch_from_midpoint > atr * self.min_stretch_factor and current_stretch_down > atr * self.max_stretch_factor:
                # Ensure we are not in a strong downtrend
                if current_price > sma: # Price should be trying to recover above short-term average
                    signal_direction = "long"
                    confidence = min(0.8, (stretch_from_midpoint / (atr * self.max_stretch_factor)))
                    reason = "session_fade_long_stretched_down"
        
        # Short fade: Price is significantly above session open, and we expect a reversion towards the midpoint
        # Only if price is above midpoint, and stretched upwards
        elif current_price > session_midpoint:
            stretch_from_midpoint = current_price - session_midpoint
            if stretch_from_midpoint > atr * self.min_stretch_factor and current_stretch_up > atr * self.max_stretch_factor:
                # Ensure we are not in a strong uptrend
                if current_price < sma: # Price should be trying to recover below short-term average
                    signal_direction = "short"
                    confidence = min(0.8, (stretch_from_midpoint / (atr * self.max_stretch_factor)))
                    reason = "session_fade_short_stretched_up"

        if signal_direction and confidence > 0.4: # Require minimum confidence
            self.last_trade_bar = ctx.bar_index
            self.traded_this_session = True
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_price": current_price,
                    "session_open_price": self.session_open_price,
                    "session_midpoint": session_midpoint,
                    "atr": atr,
                    "sma": sma,
                    "regime": regime,
                    "trend_regime": trend_regime,
                    "fear_greed_index": fear_greed_index
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt based on position close.
        # For instance, adjust max_stretch_factor or cooldown_bars based on performance.
        pass