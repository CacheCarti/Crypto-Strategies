from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class AsiaSessionDriftSOL(Strategy):
    METADATA = {
        "name": "AsiaSessionDriftSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2,  # Intended hold for 2 hours, covering next session
        "warmup_bars": 50,  # For median range and volume
    }

    def initialize(self, ctx: BarContext) -> None:
        self.max_session_len = 8 # Asia session is 8 hours (0-8 UTC)
        self.median_range_period = 30 # Bars to calculate median range
        self.min_range_multiplier = 0.8 # Minimum range for a decisive move
        self.max_range_multiplier = 2.5 # Maximum range to avoid exhausted moves
        self.min_volume_ratio = 1.2 # Require higher than average volume
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars

        # State variables for session tracking
        self.current_session_day = -1
        self.session_open_price = None
        self.session_high_price = None
        self.session_low_price = None
        self.session_volume = 0.0
        self.session_start_bar_index = -1
        self.session_direction = None # "long" or "short"
        self.session_drift_triggered = False # Flag to ensure only one entry per session

    def _median_true_range(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        return statistics.median(trs[-period:])

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)

        if len(closes) <= self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day
        
        # Check for start of a new Asia session (UTC 00:00)
        if current_hour == 0 and current_day != self.current_session_day:
            self.current_session_day = current_day
            self.session_open_price = ctx.bar.open
            self.session_high_price = ctx.bar.high
            self.session_low_price = ctx.bar.low
            self.session_volume = ctx.bar.volume
            self.session_start_bar_index = ctx.bar_index
            self.session_direction = None
            self.session_drift_triggered = False
        elif self.session_open_price is not None:
            # Update session high/low/volume
            self.session_high_price = max(self.session_high_price, ctx.bar.high)
            self.session_low_price = min(self.session_low_price, ctx.bar.low)
            self.session_volume += ctx.bar.volume

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None
        
        # Exit logic: Close position before end of Asia session (UTC 8:00)
        # or if it's already in the next session (UTC 9:00 onwards)
        if ctx.has_position() and current_hour >= 8: # Exit at the start of UTC 8:00 bar
            if ctx.position_direction() == "long" and self.session_direction == "long":
                # Only exit if this position was a session drift long
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "entry_hour": self.session_start_bar_index})
            elif ctx.position_direction() == "short" and self.session_direction == "short":
                 # Only exit if this position was a session drift short
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "entry_hour": self.session_start_bar_index})


        # Entry logic: Only consider entries within Asia session (UTC 0-7) and if not already triggered
        if self.session_open_price is not None and \
           0 <= current_hour < 8 and \
           not ctx.has_position() and \
           not self.session_drift_triggered:

            session_range = self.session_high_price - self.session_low_price
            session_change_abs = abs(ctx.bar.close - self.session_open_price)
            
            median_tr = self._median_true_range(highs, lows, closes, self.median_range_period)
            if median_tr is None or median_tr == 0:
                return None

            # Calculate average volume
            avg_volume = self._sma(volumes, self.median_range_period)
            if avg_volume is None or avg_volume == 0:
                return None
            
            current_volume_ratio = ctx.bar.volume / avg_volume

            # Check for decisive move within session, but not exhausted
            is_decisive_move = session_range > median_tr * self.min_range_multiplier and \
                               session_range < median_tr * self.max_range_multiplier and \
                               current_volume_ratio > self.min_volume_ratio

            # Avoid single-bar spikes: session change should be significant
            # and current bar close should be near session high/low for direction
            session_bars_elapsed = ctx.bar_index - self.session_start_bar_index + 1
            if session_bars_elapsed < 3: # Wait for at least 3 bars to form
                return None
            
            # Long condition: price has moved up significantly within the session
            if ctx.bar.close > self.session_open_price and is_decisive_move:
                # Ensure the current bar is confirming the upward movement
                if ctx.bar.close > ctx.bar.open and ctx.bar.close == self.session_high_price: # price is at session high
                    self.session_direction = "long"
                    self.session_drift_triggered = True
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.65,
                        metadata={
                            "reason": "asia_session_bullish_drift",
                            "session_open_price": self.session_open_price,
                            "session_high": self.session_high_price,
                            "session_low": self.session_low_price,
                            "session_range": session_range,
                            "median_tr": median_tr,
                            "current_close": ctx.bar.close,
                            "current_volume_ratio": current_volume_ratio,
                            "entry_hour": current_hour
                        }
                    )

            # Short condition: price has moved down significantly within the session
            elif ctx.bar.close < self.session_open_price and is_decisive_move:
                # Ensure the current bar is confirming the downward movement
                if ctx.bar.close < ctx.bar.open and ctx.bar.close == self.session_low_price: # price is at session low
                    self.session_direction = "short"
                    self.session_drift_triggered = True
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.65,
                        metadata={
                            "reason": "asia_session_bearish_drift",
                            "session_open_price": self.session_open_price,
                            "session_high": self.session_high_price,
                            "session_low": self.session_low_price,
                            "session_range": session_range,
                            "median_tr": median_tr,
                            "current_close": ctx.bar.close,
                            "current_volume_ratio": current_volume_ratio,
                            "entry_hour": current_hour
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session drift trigger if position closed
        self.session_drift_triggered = False
        self.last_trade_bar_index = ctx.bar_index