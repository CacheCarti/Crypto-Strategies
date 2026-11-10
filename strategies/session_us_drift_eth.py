from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionDrift(Strategy):
    METADATA = {
        "name": "US Session Drift ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for 1-hour session, but can extend
        "warmup_bars": 50,  # For median range and EMA
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13  # UTC 13:00
        self.us_session_end_hour = 21    # UTC 21:00 (exclusive, so trades close before 21:00)
        self.min_session_move_factor = 1.2 # Session move must exceed 1.2x median range
        self.max_session_move_factor = 3.0 # Session move must not exceed 3.0x median range (avoid exhaustion)
        self.median_range_period = 20    # Period for median daily range
        self.ema_period = 20             # EMA for trend confirmation
        self.min_confidence = 0.6        # Minimum confidence for a signal
        self.cooldown_bars = 5           # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        self.last_session_close_price = None
        self.last_session_end_bar_index = -1
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open = None
        self.session_active = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        ranges.sort()
        mid = len(ranges) // 2
        return (ranges[mid] + ranges[~mid]) / 2

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # Calculate EMA and Median Range
        ema = self._ema(closes, self.ema_period)
        median_range = self._median_range(highs, lows, self.median_range_period)

        if ema is None or median_range is None:
            return None

        # Session tracking logic
        if current_hour == self.us_session_start_hour and not self.session_active:
            # Start of US session
            self.session_active = True
            self.session_open = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            # Reset last session close to avoid stale data if we missed an end
            self.last_session_close_price = None 

        if self.session_active:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

            if current_hour == (self.us_session_end_hour - 1) and ctx.bar.timestamp.minute >= 55:
                # Store the session close price just before the session ends
                self.last_session_close_price = ctx.bar.close
                self.last_session_end_bar_index = current_bar_index

        if current_hour == self.us_session_end_hour and self.session_active:
            # End of US session
            self.session_active = False
            # Ensure we have a valid session close from the previous hour
            if self.last_session_close_price is None:
                self.last_session_close_price = ctx.bar.close # Fallback if 55min check failed for some reason
            
            # Reset session stats for next cycle
            self.session_open = None
            self.session_high = -math.inf
            self.session_low = math.inf

        # Exit logic: Close position before US session ends
        if ctx.has_position() and current_hour == self.us_session_end_hour -1: # Exit in the hour before session end
             # Ensure we are in the last bar of the hour before session end
            if ctx.bar.timestamp.minute >= 55:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close_time", "current_hour": current_hour})

        # Cooldown logic
        if current_bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: After US session ends, if a decisive move happened
        if not ctx.has_position() and current_hour == self.us_session_end_hour:
            if self.last_session_close_price is not None and self.session_open is not None and median_range > 0:
                session_move = self.last_session_close_price - self.session_open
                session_range = self.session_high - self.session_low

                # Check if session move was decisive but not exhausted
                if abs(session_move) > self.min_session_move_factor * median_range and \
                   abs(session_move) < self.max_session_move_factor * median_range and \
                   session_range > 0: # Avoid division by zero

                    # Avoid single-bar spikes: check if the move was sustained
                    # (e.g., if current bar is not a reversal of the session move)
                    if (session_move > 0 and ctx.bar.close > ctx.bar.open) or \
                       (session_move < 0 and ctx.bar.close < ctx.bar.open):

                        # Trend confirmation with EMA
                        if session_move > 0 and ctx.bar.close > ema:  # Bullish drift
                            self.last_trade_bar = current_bar_index
                            return ctx.signal(
                                "long",
                                confidence=self.min_confidence,
                                metadata={
                                    "reason": "us_session_bullish_drift",
                                    "session_open": self.session_open,
                                    "session_close": self.last_session_close_price,
                                    "session_move_usd": session_move,
                                    "median_range_usd": median_range,
                                    "ema_20": ema,
                                    "current_price": ctx.bar.close
                                }
                            )
                        elif session_move < 0 and ctx.bar.close < ema:  # Bearish drift
                            self.last_trade_bar = current_bar_index
                            return ctx.signal(
                                "short",
                                confidence=self.min_confidence,
                                metadata={
                                    "reason": "us_session_bearish_drift",
                                    "session_open": self.session_open,
                                    "session_close": self.last_session_close_price,
                                    "session_move_usd": session_move,
                                    "median_range_usd": median_range,
                                    "ema_20": ema,
                                    "current_price": ctx.bar.close
                                }
                            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Use for logging or adaptive sizing
        pass