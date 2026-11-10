from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionImpulseBTC(Strategy):
    METADATA = {
        "name": "Asia Session Impulse BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Max hold 2 hours, exits before 8 UTC
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_session_range_pct = 0.25 # Minimum session range % to consider a signal
        self.cooldown_bars = 6
        self.last_signal_bar = -self.cooldown_bars
        self.session_start_bar_index = -1
        self.session_high = -1.0
        self.session_low = -1.0
        self.session_open = -1.0
        self.session_close = -1.0
        self.session_direction = None # "long" or "short"
        self.last_signal_direction = None

        # Parameters for regime filters
        self.min_volatility_score = 0.3 # Only trade if market is not too calm
        self.max_crisis_score = 0.5 # Avoid trading in extreme crisis
        self.min_trend_confidence = 0.4 # Require some confidence in trend regime
        self.funding_rate_threshold = 0.0001 # 0.01% for funding rate bias

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        highs = ctx.highs(self.METADATA["warmup_bars"] + 1)
        lows = ctx.lows(self.METADATA["warmup_bars"] + 1)
        opens = ctx.opens(self.METADATA["warmup_bars"] + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check cooldown
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Regime filters
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)

        # Do not trade in crisis or if trend confidence is too low
        if crisis_score > self.max_crisis_score:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "exit_crisis_regime", "crisis_score": crisis_score})
            return None

        # Asia Session (UTC 0-8) logic
        # Start of Asia Session (0 UTC) - Reset session data
        if current_hour == 0 and self.session_start_bar_index != ctx.bar_index:
            self.session_start_bar_index = ctx.bar_index
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_open = ctx.bar.open
            self.session_close = ctx.bar.close # Will be updated throughout the session
            self.session_direction = None
        elif self.session_start_bar_index != -1 and current_hour >= 0 and current_hour < 8:
            # Update session high/low/close
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_close = ctx.bar.close

        # End of Asia Session (8 UTC) - Evaluate impulse and potentially enter
        if current_hour == 8 and self.session_start_bar_index != -1 and self.session_direction is None:
            # Only evaluate once at the start of hour 8
            session_range = self.session_high - self.session_low
            session_range_pct = (session_range / self.session_open) * 100 if self.session_open > 0 else 0

            if session_range_pct < self.min_session_range_pct:
                self.session_start_bar_index = -1 # Reset for next day
                return None # Session range too small to be significant

            # Determine session impulse
            if self.session_close > self.session_open + session_range * 0.2: # Bullish impulse (closed significantly above open)
                self.session_direction = "long"
            elif self.session_close < self.session_open - session_range * 0.2: # Bearish impulse (closed significantly below open)
                self.session_direction = "short"
            else:
                self.session_direction = "neutral" # No clear impulse

            # If clear impulse, look for a confirmation bar at 8 UTC
            if self.session_direction in ["long", "short"] and not ctx.has_position():
                # Entry confirmation: current bar confirms direction
                entry_signal = None
                confidence = 0.6

                # Apply funding rate bias
                funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
                if self.session_direction == "long" and funding_rate < -self.funding_rate_threshold:
                    confidence += 0.1 # Negative funding rate makes long more attractive
                elif self.session_direction == "short" and funding_rate > self.funding_rate_threshold:
                    confidence += 0.1 # Positive funding rate makes short more attractive

                if self.session_direction == "long" and ctx.bar.close > ctx.bar.open:
                    entry_signal = ctx.signal(
                        "long",
                        confidence=min(1.0, confidence),
                        metadata={
                            "reason": "asia_impulse_long_entry",
                            "session_open": self.session_open,
                            "session_close": self.session_close,
                            "session_high": self.session_high,
                            "session_low": self.session_low,
                            "session_range_pct": session_range_pct,
                            "current_close": ctx.bar.close,
                            "funding_rate": funding_rate,
                        }
                    )
                elif self.session_direction == "short" and ctx.bar.close < ctx.bar.open:
                    entry_signal = ctx.signal(
                        "short",
                        confidence=min(1.0, confidence),
                        metadata={
                            "reason": "asia_impulse_short_entry",
                            "session_open": self.session_open,
                            "session_close": self.session_close,
                            "session_high": self.session_high,
                            "session_low": self.session_low,
                            "session_range_pct": session_range_pct,
                            "current_close": ctx.bar.close,
                            "funding_rate": funding_rate,
                        }
                    )

                if entry_signal:
                    self.last_signal_bar = ctx.bar_index
                    self.last_signal_direction = self.session_direction
                    self.session_start_bar_index = -1 # Reset for next day
                    return entry_signal
            else:
                self.session_start_bar_index = -1 # Reset if no entry or no clear impulse

        # Position management
        if ctx.has_position():
            # Exit any open position before 8 UTC on the next day, or if it's the wrong direction
            if current_hour == 8 and self.last_signal_direction and ctx.position_direction() == self.last_signal_direction:
                # This is the 8 UTC bar of the day AFTER the signal. Exit to ensure holding for 24h is not the plan.
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "exit_session_boundary", "current_hour": current_hour})

        # Reset session data at 9 UTC to ensure no lingering state
        if current_hour == 9 and self.session_start_bar_index != -1:
            self.session_start_bar_index = -1
            self.session_direction = None

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close
        self.last_signal_bar = ctx.bar_index
        self.last_signal_direction = None