from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionFadeStrategy(Strategy):
    METADATA = {
        "name": "Session Fade Strategy",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for 1-2 hour hold
        "warmup_bars": 50, # Need enough bars for 20-period calculations
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 18 # UTC 18:00 (late US session start)
        self.session_end_hour = 23   # UTC 23:00 (trade up to this hour, close before 00:00)
        self.entry_window_start = 20 # Only enter between 20:00 and 23:00 UTC
        self.entry_window_end = 22   # Last entry at 22:xx UTC
        self.min_session_range_pct = 0.5 # Min % range for session to be considered stretched
        self.max_session_range_pct = 3.0 # Max % range to avoid extreme volatility
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open = None
        self.session_close_prices = [] # Store prices during the session
        self.session_midpoint = None
        self.traded_today = False # To ensure only one entry per session

        # Indicators for trend and volatility
        self.ema_period_long = 40
        self.stddev_period = 20

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
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Check for start of a new session (or new day)
        if current_hour == self.session_start_hour and not self.traded_today:
            # New session starts, reset session data
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_open = ctx.bar.open
            self.session_close_prices = [ctx.bar.close]
            self.traded_today = False
        elif current_hour == 0 and self.traded_today: # Reset traded_today flag at UTC midnight
            self.traded_today = False

        # Update session high/low/prices if within session
        if self.session_start_hour <= current_hour <= self.session_end_hour:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_close_prices.append(ctx.bar.close)
            if self.session_open is not None:
                self.session_midpoint = (self.session_high + self.session_low) / 2.0
        else: # Outside session hours, reset for next session
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_open = None
            self.session_close_prices = []
            self.session_midpoint = None

        # Logic for exiting before end of session
        if ctx.has_position() and ctx.position_direction() in ["long", "short"]:
            if current_hour == self.session_end_hour + 1: # Close at 00:00 UTC
                return ctx.signal("flat", confidence=0.9, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only consider entry during the entry window and if no position
        if not ctx.has_position() and \
           self.entry_window_start <= current_hour <= self.entry_window_end and \
           self.session_open is not None and \
           self.session_midpoint is not None and \
           not self.traded_today:

            # Regime filters
            regime = ctx.market.get("regime", "NORMAL")
            fear_greed = ctx.features.get("fear_greed_index", 50)
            trend_regime = ctx.market.get("trend_regime", "neutral")

            # Avoid crisis and strong trends
            if regime == "CRISIS" or trend_regime in ["bull", "bear"]:
                return None

            # Calculate session range and current position relative to midpoint
            session_range = self.session_high - self.session_low
            current_range_pct = (session_range / self.session_open) * 100 if self.session_open else 0

            # Check if session is stretched but not excessively volatile
            if not (self.min_session_range_pct <= current_range_pct <= self.max_session_range_pct):
                return None

            # Check for stalling momentum using a short-term average
            # Compare current price to a short EMA of session prices
            if len(self.session_close_prices) < 5: # Need at least 5 bars for this check
                return None
            
            # Calculate volatility for confidence
            stddev = self._stddev(closes, self.stddev_period)
            if stddev is None or stddev == 0:
                return None
            
            # Entry logic
            signal_direction = None
            reason = ""
            confidence = 0.0

            # Fade a stretched move up
            if ctx.bar.close > self.session_midpoint * (1 + current_range_pct * 0.002) and \
               ctx.bar.close > self._ema(self.session_close_prices, 3) and \
               ctx.bar.close > self.session_open: # Must be above session open to be a "move up"
                
                # Further check if price is stalling near session high
                if ctx.bar.close < self.session_high * 0.995: # Not at absolute high, some pullback
                    signal_direction = "short"
                    reason = "fade_session_upside_stall"
                    confidence = min(0.8, current_range_pct / (self.max_session_range_pct / 2)) # Higher confidence for more stretched moves

            # Fade a stretched move down
            elif ctx.bar.close < self.session_midpoint * (1 - current_range_pct * 0.002) and \
                 ctx.bar.close < self._ema(self.session_close_prices, 3) and \
                 ctx.bar.close < self.session_open: # Must be below session open to be a "move down"

                # Further check if price is stalling near session low
                if ctx.bar.close > self.session_low * 1.005: # Not at absolute low, some bounce
                    signal_direction = "long"
                    reason = "fade_session_downside_stall"
                    confidence = min(0.8, current_range_pct / (self.max_session_range_pct / 2)) # Higher confidence for more stretched moves

            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                self.traded_today = True
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "current_hour": current_hour,
                        "session_open": self.session_open,
                        "session_high": self.session_high,
                        "session_low": self.session_low,
                        "session_midpoint": self.session_midpoint,
                        "current_price": ctx.bar.close,
                        "session_range_pct": current_range_pct,
                        "regime": regime,
                        "fear_greed": fear_greed,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: log position close for analysis or adaptive logic
        pass