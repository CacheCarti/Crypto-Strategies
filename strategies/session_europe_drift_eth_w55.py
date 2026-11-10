from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanSessionBreakout(Strategy):
    METADATA = {
        "name": "European Session Breakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-hour hold, targeting next session
        "warmup_bars": 50, # Need enough bars for ATR and median range
        "required_features": [], # No external features needed for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_open_hour = 7  # UTC 7:00
        self.session_close_hour = 12 # UTC 12:00
        self.min_move_atr_multiplier = 0.5 # Minimum move to consider a breakout, in ATRs
        self.cooldown_bars = 5 # Cooldown after a trade, to prevent overtrading
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        self.atr_period = 20
        self.median_range_period = 40 # For median range calculation
        self.max_spike_ratio = 0.6 # Max ratio of current bar range to total move to avoid spikes

        # State variables for session logic
        self.session_high = -1.0
        self.session_low = float('inf')
        self.session_open_price = -1.0
        self.session_start_bar_index = -1
        self.last_signal_day = -1

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _median(self, data):
        if not data:
            return None
        sorted_data = sorted(data)
        n = len(sorted_data)
        if n % 2 == 1:
            return sorted_data[n // 2]
        else:
            mid1 = sorted_data[n // 2 - 1]
            mid2 = sorted_data[n // 2]
            return (mid1 + mid2) / 2

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Update session high/low if within session hours
        if self.session_open_hour <= current_hour < self.session_close_hour:
            if current_day != self.last_signal_day: # Reset session data for a new day
                self.session_high = ctx.bar.high
                self.session_low = ctx.bar.low
                self.session_open_price = ctx.bar.open
                self.session_start_bar_index = ctx.bar_index
                self.last_signal_day = current_day
            else:
                self.session_high = max(self.session_high, ctx.bar.high)
                self.session_low = min(self.session_low, ctx.bar.low)
        elif current_hour == self.session_close_hour and ctx.bar_index > self.session_start_bar_index:
            # This is the bar immediately after the session close, where we look for a breakout
            pass
        else: # Outside trading hours, reset session tracking
            self.session_high = -1.0
            self.session_low = float('inf')
            self.session_open_price = -1.0
            self.session_start_bar_index = -1

        # Check for cooldown
        if ctx.bar_index - self.last_trade_bar <= self.cooldown_bars:
            return None

        # Logic for entry at the session close hour (e.g., 12 UTC)
        if current_hour == self.session_close_hour and self.session_start_bar_index != -1 and not ctx.has_position():
            # Calculate ATR for minimum move threshold
            current_atr = self._atr(highs, lows, closes, self.atr_period)
            if current_atr is None:
                return None

            # Calculate median true range of recent bars for dynamic threshold
            recent_ranges = [h - l for h, l in zip(highs[-self.median_range_period:], lows[-self.median_range_period:])]
            median_range = self._median(recent_ranges)
            if median_range is None:
                return None

            min_move_threshold = max(current_atr * self.min_move_atr_multiplier, median_range * 0.5) # Use a blend

            session_range = self.session_high - self.session_low
            session_move_up = self.session_high - self.session_open_price
            session_move_down = self.session_open_price - self.session_low

            # Check for decisive move and avoid single-bar spikes
            is_spike = (ctx.bar.high - ctx.bar.low) / session_range > self.max_spike_ratio if session_range > 0 else False

            signal_direction = None
            reason = ""
            confidence = 0.0

            # Long condition: price broke above session high with a decisive move
            if ctx.bar.close > self.session_high and session_move_up > min_move_threshold and not is_spike:
                signal_direction = "long"
                confidence = min(1.0, session_move_up / (current_atr * 2)) # Confidence scales with strength of move
                reason = "european_session_breakout_long"

            # Short condition: price broke below session low with a decisive move
            elif ctx.bar.close < self.session_low and session_move_down > min_move_threshold and not is_spike:
                signal_direction = "short"
                confidence = min(1.0, session_move_down / (current_atr * 2)) # Confidence scales with strength of move
                reason = "european_session_breakout_short"

            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "session_high": self.session_high,
                        "session_low": self.session_low,
                        "session_open_price": self.session_open_price,
                        "current_close": ctx.bar.close,
                        "current_atr": current_atr,
                        "min_move_threshold": min_move_threshold,
                        "is_spike": is_spike,
                        "median_range": median_range,
                    }
                )

        # Exit logic: if holding a position, exit by the end of the next session or if structure breaks
        if ctx.has_position() and current_hour == self.session_close_hour and ctx.position_direction() is not None:
            # Exit at the close of the session after entry
            # This is a simple time-based exit. More complex structure stops could be added.
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": "time_based_session_exit",
                    "current_hour": current_hour,
                    "session_close_hour": self.session_close_hour,
                    "current_close": ctx.bar.close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally log or adapt based on position outcome
        # For this strategy, we mainly use last_trade_bar for cooldown in on_bar
        pass