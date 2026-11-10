from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionDrift(Strategy):
    METADATA = {
        "name": "AsiaSessionDrift",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2,  # Intended hold is ~2 hours, maybe more if trend continues
        "warmup_bars": 50,  # Need enough bars for median range and ATR
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.median_range_period = 20
        self.min_session_move_factor = 0.5  # Min move as factor of median range
        self.max_session_move_factor = 3.0   # Max move as factor of median range (avoid exhaustion)
        self.max_single_bar_move_factor = 0.75 # Max single bar move as factor of session move
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_signal_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # Track session state
        self.asia_session_open_price = None
        self.asia_session_high = None
        self.asia_session_low = None
        self.last_asia_close_time = None
        self.last_trade_direction = None

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _median(self, values):
        s = sorted(values)
        mid = len(s) // 2
        return (s[mid-1] + s[mid]) / 2 if len(s) % 2 == 0 else s[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        is_asia_session = 0 <= current_hour < 8 # UTC 0-7 is Asia session for this strategy

        # Calculate median daily range for context (using ATR as proxy for range)
        atr_val = self._atr(highs, lows, closes, self.median_range_period)
        if atr_val is None:
            return None
        median_daily_range = atr_val # Using ATR as a proxy for recent median range

        # --- Session Tracking ---
        # Start of Asia session
        if is_asia_session and self.asia_session_open_price is None:
            self.asia_session_open_price = ctx.bar.open
            self.asia_session_high = ctx.bar.high
            self.asia_session_low = ctx.bar.low

        # During Asia session
        if is_asia_session and self.asia_session_open_price is not None:
            self.asia_session_high = max(self.asia_session_high, ctx.bar.high)
            self.asia_session_low = min(self.asia_session_low, ctx.bar.low)
            self.last_asia_close_time = ctx.bar.timestamp

        # End of Asia session (or first bar after it)
        # Check if we were in Asia session and now we are not
        if not is_asia_session and self.asia_session_open_price is not None and self.last_asia_close_time is not None and self.last_asia_close_time.hour == 7: # Last hour of Asia session
            # Asia session has just ended, evaluate for a signal
            session_close = ctx.bar.close # The first bar outside Asia session, or the last bar of Asia session
            session_open = self.asia_session_open_price
            session_move = session_close - session_open
            session_range = self.asia_session_high - self.asia_session_low

            # Reset session state for the next day
            self.asia_session_open_price = None
            self.asia_session_high = None
            self.asia_session_low = None
            self.last_asia_close_time = None # Reset after processing

            # --- Signal Logic ---
            if ctx.bar_index < self.last_signal_bar_index + self.cooldown_bars:
                return None # Cooldown active

            # Check for decisive move
            min_decisive_move = median_daily_range * self.min_session_move_factor
            max_decisive_move = median_daily_range * self.max_session_move_factor

            # Check if the move was not a single-bar spike
            # This is a bit tricky with 1h bars. We'll check if the *last bar of Asia*
            # wasn't too large a portion of the total session move.
            # For simplicity, we'll check if the current bar's move isn't too large compared to the session move.
            # This implicitly checks the last bar of Asia if the signal fires on the first bar *after* Asia.
            current_bar_move = abs(ctx.bar.close - ctx.bar.open)
            if abs(session_move) > 0 and current_bar_move / abs(session_move) > self.max_single_bar_move_factor:
                # print(f"Skipping due to single bar spike: current_bar_move={current_bar_move}, session_move={session_move}")
                return None # Avoid single-bar spikes

            if abs(session_move) > min_decisive_move and abs(session_move) < max_decisive_move:
                confidence = min(0.9, abs(session_move) / median_daily_range) # Scale confidence by move size

                if session_move > 0: # Bullish Asia session, look for long
                    if not ctx.has_position() or ctx.position_direction() == "short":
                        self.last_signal_bar_index = ctx.bar_index
                        self.last_trade_direction = "long"
                        return ctx.signal("long", confidence=confidence,
                                          metadata={"reason": "asia_session_drift_long",
                                                    "session_move": session_move,
                                                    "median_range": median_daily_range,
                                                    "current_hour": current_hour,
                                                    "close_price": ctx.bar.close})
                elif session_move < 0: # Bearish Asia session, look for short
                    if not ctx.has_position() or ctx.position_direction() == "long":
                        self.last_signal_bar_index = ctx.bar_index
                        self.last_trade_direction = "short"
                        return ctx.signal("short", confidence=confidence,
                                          metadata={"reason": "asia_session_drift_short",
                                                    "session_move": session_move,
                                                    "median_range": median_daily_range,
                                                    "current_hour": current_hour,
                                                    "close_price": ctx.bar.close})

        # --- Position Management ---
        if ctx.has_position():
            # Exit before the next Asia session if still holding
            if current_hour == 23: # Last hour of the day before Asia session starts
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "pre_asia_session_exit", "current_hour": current_hour, "close_price": ctx.bar.close})

            # Check for structure stop - if price reverts heavily back into Asia session range
            if self.last_trade_direction == "long" and self.asia_session_open_price is not None:
                # If price drops significantly below the Asia session open after we went long
                if ctx.bar.close < self.asia_session_open_price * (1 - self.METADATA["declared_sl_bps"]/10000 * 0.75): # 75% of SL as a soft structure stop
                    self.last_signal_bar_index = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "structure_stop_long", "close_price": ctx.bar.close, "asia_open": self.asia_session_open_price})
            elif self.last_trade_direction == "short" and self.asia_session_open_price is not None:
                # If price rises significantly above the Asia session open after we went short
                if ctx.bar.close > self.asia_session_open_price * (1 + self.METADATA["declared_sl_bps"]/10000 * 0.75):
                    self.last_signal_bar_index = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "structure_stop_short", "close_price": ctx.bar.close, "asia_open": self.asia_session_open_price})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade direction on close
        self.last_trade_direction = None
        # You could also implement adaptive sizing here based on return_bps
        pass