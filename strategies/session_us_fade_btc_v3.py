from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionUSFadeBTC(Strategy):
    METADATA = {
        "name": "SessionUSFadeBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2,  # Intended hold is within the session, but can spill over slightly
        "warmup_bars": 50,  # Need enough data for EMAs and session range
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"] # Using these as filters
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_open_hour = 13  # UTC 13:00 - US session open
        self.session_close_hour = 21  # UTC 21:00 - US session close
        self.midpoint_period = 5  # EMA period for session midpoint
        self.range_period = 20 # Bars to calculate session range
        self.cooldown_bars = 5  # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # State to track for session logic
        self.session_high = -1.0
        self.session_low = float('inf')
        self.session_open_price = -1.0
        self.session_midpoint = -1.0
        self.session_started = False
        self.traded_this_session = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        # Calculate initial SMA for the first EMA value
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Session tracking logic
        if current_hour == self.session_open_hour and not self.session_started:
            # New session starts
            self.session_started = True
            self.traded_this_session = False
            self.session_open_price = current_close
            self.session_high = current_close
            self.session_low = current_close
            self.session_midpoint = current_close # Initial midpoint
        elif current_hour != self.session_open_hour and self.session_started:
            # During session, update high/low
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            # Update session midpoint dynamically with EMA
            session_closes_since_open = []
            # This is a bit inefficient, but keeps within BarContext limits
            # In a real system, you'd store all bar closes since session open
            # For dMoERA, we approximate with recent closes if within session
            if ctx.bar_index > self.METADATA["warmup_bars"]:
                # Get closes from current hour back to session open hour for current day
                # This is a rough approximation, not perfect for full session history
                for i in range(1, min(ctx.bar_index, 24) + 1): # Look back up to 24 bars
                    if (ctx.bar.timestamp - ctx.bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 3600 < i:
                        break # Went past the start of the day
                    if ctx.bar.timestamp.hour - i >= self.session_open_hour:
                        session_closes_since_open.append(ctx.closes(i)[0])
                    else:
                        break
                session_closes_since_open.reverse() # Oldest first
                if len(session_closes_since_open) >= self.midpoint_period:
                    self.session_midpoint = self._ema(session_closes_since_open, self.midpoint_period)
                else:
                    self.session_midpoint = (self.session_high + self.session_low) / 2 # Fallback to simple midpoint
            else:
                self.session_midpoint = (self.session_high + self.session_low) / 2 # Fallback for early bars

        # Reset session tracking at the end of the session
        if current_hour == self.session_close_hour and self.session_started:
            if ctx.has_position():
                # Exit any open position before session close
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_close_exit",
                    "current_hour": current_hour,
                    "session_open_price": self.session_open_price,
                    "session_midpoint": self.session_midpoint
                })
            self.session_started = False
            self.traded_this_session = False
            self.session_high = -1.0
            self.session_low = float('inf')
            self.session_open_price = -1.0
            self.session_midpoint = -1.0

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Only trade during US session and if not traded yet
        if not self.session_started or self.traded_this_session or current_hour < self.session_open_hour + 1: # Wait an hour after open
            return None

        # Do not fade in crisis or strong trend regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        if market_regime in ["CRISIS", "MELTDOWN"] or trend_regime in ["bull", "bear"]:
            return None # Avoid fading strong trends or crises

        # Calculate session range and current extension
        session_range = self.session_high - self.session_low
        if session_range <= 0: # Avoid division by zero
            return None

        # Define "stretched" based on current price relative to session range
        # Look for price to be in the top/bottom X% of the range
        stretch_threshold = 0.7  # e.g., beyond 70% of the range from the midpoint
        min_stretch_bps = 50 # Minimum stretch in bps to consider a fade

        # Calculate recent volatility (ATR like) to ensure enough movement
        if len(highs) < self.range_period + 1: return None
        true_ranges = []
        for i in range(1, self.range_period):
            tr = max(highs[-i]-lows[-i], abs(highs[-i]-closes[-i-1]), abs(lows[-i]-closes[-i-1]))
            true_ranges.append(tr)
        avg_true_range = sum(true_ranges) / len(true_ranges) if true_ranges else 0

        # Require a minimum range for the session to be tradable
        if session_range < avg_true_range * 1.5: # Session range must be at least 1.5x average true range
             return None

        # Fading logic
        signal_confidence = 0.0
        signal_direction = "hold"
        reason = "no_signal"

        # Check for long fade (price stretched to the downside, fade back up)
        if current_close < self.session_low + session_range * (1 - stretch_threshold):
            stretch_bps = (self.session_low + session_range * (1 - stretch_threshold) - current_close) / current_close * 10000
            if stretch_bps > min_stretch_bps:
                # Only if price is below session midpoint and funding is not too negative (avoid strong downtrends)
                if current_close < self.session_midpoint and funding_rate > -0.0001:
                    signal_direction = "long"
                    # Confidence scales with how far stretched it is, capped at 1.0
                    signal_confidence = min(0.5 + (stretch_bps - min_stretch_bps) / 100.0, 0.9)
                    reason = "session_fade_long_oversold"

        # Check for short fade (price stretched to the upside, fade back down)
        elif current_close > self.session_high - session_range * (1 - stretch_threshold):
            stretch_bps = (current_close - (self.session_high - session_range * (1 - stretch_threshold))) / current_close * 10000
            if stretch_bps > min_stretch_bps:
                # Only if price is above session midpoint and funding is not too positive (avoid strong uptrends)
                if current_close > self.session_midpoint and funding_rate < 0.0001:
                    signal_direction = "short"
                    # Confidence scales with how far stretched it is, capped at 1.0
                    signal_confidence = min(0.5 + (stretch_bps - min_stretch_bps) / 100.0, 0.9)
                    reason = "session_fade_short_overbought"

        if signal_direction != "hold" and not ctx.has_position():
            self.traded_this_session = True
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                metadata={
                    "reason": reason,
                    "current_price": current_close,
                    "session_open_price": self.session_open_price,
                    "session_high": self.session_high,
                    "session_low": self.session_low,
                    "session_midpoint": self.session_midpoint,
                    "session_range": session_range,
                    "stretch_bps": stretch_bps,
                    "fear_greed_index": fear_greed,
                    "funding_rate": funding_rate
                }
            )

        # If we have a position, check if we should flat it early if midpoint is reached
        if ctx.has_position() and ctx.position_direction() == "long" and current_close >= self.session_midpoint:
            return ctx.signal("flat", confidence=0.7, metadata={
                "reason": "long_fade_target_midpoint_hit",
                "current_price": current_close,
                "session_midpoint": self.session_midpoint,
                "entry_price": ctx.position["entry_price"]
            })
        elif ctx.has_position() and ctx.position_direction() == "short" and current_close <= self.session_midpoint:
            return ctx.signal("flat", confidence=0.7, metadata={
                "reason": "short_fade_target_midpoint_hit",
                "current_price": current_close,
                "session_midpoint": self.session_midpoint,
                "entry_price": ctx.position["entry_price"]
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset traded_this_session if position closed for any reason
        # This allows potentially re-entering if conditions reset, but the primary logic
        # still prevents re-entry within the same session if self.traded_this_session is True.
        # This is here for robustness, though the main logic prevents re-entry anyway.
        # Could be used for adaptive logic, e.g., if position was profitable, increase confidence.
        pass