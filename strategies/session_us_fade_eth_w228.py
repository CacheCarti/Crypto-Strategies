from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionFadeStrategy(Strategy):
    METADATA = {
        "name": "US Session Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for 1-hour session holds
        "warmup_bars": 50,              # Enough for EMA50 and ATR
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_midpoint_ema_period = 20 # EMA for session midpoint reference
        self.atr_period = 14
        self.max_session_stretch_atr = 2.0 # How many ATRs price can move before considered stretched
        self.min_session_stretch_atr = 0.5 # Minimum stretch to consider fading
        self.cooldown_bars = 5 # Wait N bars after a trade before new entry
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        self.last_session_day = -1 # To track new session day

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset session tracking at the start of a new day
        if current_day != self.last_session_day:
            self.last_session_day = current_day
            self.session_open_price = None # Reset session open
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_bars = [] # To store bars within the session
            self.session_midpoint_ema = None


        # US Session is UTC 13:00 to 21:00 (inclusive of 13, exclusive of 21 for entry)
        is_us_session_entry_hour = 13 <= current_hour < 21
        is_us_session_exit_hour = 20 <= current_hour # Exit by 21:00 UTC

        # Only consider trading if not in cooldown
        if ctx.bar_index <= self.last_trade_bar_index + self.cooldown_bars:
            # If we are in a position, we can still manage it
            if ctx.has_position() and is_us_session_exit_hour:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close_exit", "current_hour": current_hour})
            return None # Skip entry if in cooldown

        current_price = ctx.bar.close

        # Update session data
        if is_us_session_entry_hour:
            if self.session_open_price is None:
                self.session_open_price = ctx.bar.open
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_bars.append(current_price)

            # Calculate session midpoint EMA
            if len(self.session_bars) >= self.session_midpoint_ema_period:
                self.session_midpoint_ema = self._ema(self.session_bars, self.session_midpoint_ema_period)
            elif len(self.session_bars) > 0: # If not enough for EMA, use simple average
                 self.session_midpoint_ema = sum(self.session_bars) / len(self.session_bars)

        # Exit condition if we are in a position and session is closing
        if ctx.has_position() and is_us_session_exit_hour:
            return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close_exit", "current_hour": current_hour})

        # Entry logic: Only within US session entry hours and if not already in a position
        if is_us_session_entry_hour and not ctx.has_position() and self.session_open_price is not None and self.session_midpoint_ema is not None:
            
            current_atr = self._atr(highs, lows, closes, self.atr_period)
            if current_atr is None or current_atr == 0:
                return None

            # Get market regime
            trend_regime = ctx.market.get("trend_regime", "neutral")
            crisis_score = ctx.market.get("crisis_score", 0.0)

            # Do not fade strong trends or crisis regimes
            if trend_regime in ["bull", "bear"] or crisis_score > 0.3:
                return None

            # Calculate stretch relative to session open or midpoint
            session_range = self.session_high - self.session_low
            
            # Use the session midpoint EMA as the reference for "mean reversion"
            # If price is far above midpoint, look for short. If far below, look for long.
            
            stretch_from_midpoint = (current_price - self.session_midpoint_ema) / current_atr

            # Long entry: price is significantly below session midpoint EMA, stretched downwards
            if stretch_from_midpoint < -self.max_session_stretch_atr and session_range > current_atr * self.min_session_stretch_atr:
                # Confirm we are not in a strong downtrend
                if trend_regime != "bear":
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.8,
                        metadata={
                            "reason": "session_fade_long",
                            "current_hour": current_hour,
                            "session_open": self.session_open_price,
                            "session_midpoint_ema": self.session_midpoint_ema,
                            "stretch_atr": stretch_from_midpoint,
                            "current_atr": current_atr,
                            "trend_regime": trend_regime,
                            "crisis_score": crisis_score
                        }
                    )
            
            # Short entry: price is significantly above session midpoint EMA, stretched upwards
            elif stretch_from_midpoint > self.max_session_stretch_atr and session_range > current_atr * self.min_session_stretch_atr:
                # Confirm we are not in a strong uptrend
                if trend_regime != "bull":
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.8,
                        metadata={
                            "reason": "session_fade_short",
                            "current_hour": current_hour,
                            "session_open": self.session_open_price,
                            "session_midpoint_ema": self.session_midpoint_ema,
                            "stretch_atr": stretch_from_midpoint,
                            "current_atr": current_atr,
                            "trend_regime": trend_regime,
                            "crisis_score": crisis_score
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log the close for debugging or adaptive logic
        pass