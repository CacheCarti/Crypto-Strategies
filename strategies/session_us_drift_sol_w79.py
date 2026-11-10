from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class SolUsSessionBreakout(Strategy):
    METADATA = {
        "name": "SOLUSDC US Session Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours, or until end of session
        "warmup_bars": 50,
        "required_features": [], # No specific features needed beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 13 # UTC 13:00 (9 AM EST)
        self.session_end_hour = 21   # UTC 21:00 (5 PM EST)
        self.cooldown_bars = 5       # Bars to wait after a trade before re-entering
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade
        self.median_range_period = 20 # Period for calculating median true range
        self.min_session_move_factor = 1.0 # Session move must exceed 1.0 * median_range

        # Store session open/high/low/close and volume
        self.session_open = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_volume = 0.0
        self.session_bars_count = 0
        self.session_direction = None # "long" or "short" if a decisive move is detected
        self.last_session_entry_bar = -1 # Prevent multiple entries in the same session

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _median_true_range(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        true_ranges = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            true_ranges.append(tr)
        if len(true_ranges) < period:
            return None
        return statistics.median(true_ranges[-period:])

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.median_range_period + 1)
        highs = ctx.highs(self.median_range_period + 1)
        lows = ctx.lows(self.median_range_period + 1)
        volumes = ctx.volumes(self.median_range_period + 1)

        if len(closes) < self.median_range_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for end of previous session or start of new session
        if current_hour == self.session_start_hour and ctx.bar_index != self.last_session_entry_bar:
            # Start of a new session
            self.session_open = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_volume = ctx.bar.volume
            self.session_bars_count = 1
            self.session_direction = None # Reset for new session
            self.last_session_entry_bar = -1 # Reset entry flag for new session
        elif current_hour != self.session_start_hour and self.session_open is None:
            # If we missed the session start (e.g., started backtest mid-session),
            # or if it's not the US session, don't track session data.
            pass
        elif self.session_open is not None:
            # Update session high/low/volume
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_volume += ctx.bar.volume
            self.session_bars_count += 1

        # Logic to exit existing position before session end
        if ctx.has_position() and current_hour == self.session_end_hour and ctx.position_direction() is not None:
            # Exit at the end of the session
            return ctx.signal("flat", confidence=0.8, metadata={
                "reason": "session_end_exit",
                "current_hour": current_hour,
                "entry_price": ctx.position["entry_price"]
            })

        # Do not generate new signals if in cooldown
        if ctx.bar_index - self.last_trade_bar <= self.cooldown_bars:
            return None

        # Determine if we should enter a position
        if not ctx.has_position() and self.session_open is not None and self.session_bars_count >= 3 and current_hour >= self.session_start_hour and current_hour < self.session_end_hour:
            
            median_tr = self._median_true_range(highs[:-1], lows[:-1], closes[:-1], self.median_range_period)
            if median_tr is None or median_tr == 0:
                return None

            session_range = self.session_high - self.session_low
            
            # Avoid single-bar spikes by checking if the current bar is a significant portion of the session move
            # This is a heuristic to ensure the move is more sustained.
            current_bar_range = ctx.bar.high - ctx.bar.low
            if session_range > 0 and current_bar_range / session_range > 0.7: # If current bar accounts for >70% of session range, might be a spike
                 return None

            # Check for decisive move
            decisive_move_threshold = median_tr * self.min_session_move_factor

            # Long condition: price breaks above session high and move is decisive
            if ctx.bar.close > self.session_high and (ctx.bar.close - self.session_open) > decisive_move_threshold:
                # Ensure it's not exhausted (e.g., price not too far from session high)
                if (ctx.bar.close - self.session_high) / median_tr < 1.5: # Not too far above breakout
                    self.last_trade_bar = ctx.bar_index
                    self.last_session_entry_bar = ctx.bar_index # Mark entry for this session
                    return ctx.signal("long", confidence=0.75, metadata={
                        "reason": "us_session_breakout_long",
                        "session_open": self.session_open,
                        "session_high": self.session_high,
                        "current_close": ctx.bar.close,
                        "median_tr": median_tr,
                        "decisive_move_threshold": decisive_move_threshold,
                        "session_bars": self.session_bars_count,
                        "current_hour": current_hour
                    })
            
            # Short condition: price breaks below session low and move is decisive
            elif ctx.bar.close < self.session_low and (self.session_open - ctx.bar.close) > decisive_move_threshold:
                # Ensure it's not exhausted (e.g., price not too far from session low)
                if (self.session_low - ctx.bar.close) / median_tr < 1.5: # Not too far below breakout
                    self.last_trade_bar = ctx.bar_index
                    self.last_session_entry_bar = ctx.bar_index # Mark entry for this session
                    return ctx.signal("short", confidence=0.75, metadata={
                        "reason": "us_session_breakdown_short",
                        "session_open": self.session_open,
                        "session_low": self.session_low,
                        "current_close": ctx.bar.close,
                        "median_tr": median_tr,
                        "decisive_move_threshold": decisive_move_threshold,
                        "session_bars": self.session_bars_count,
                        "current_hour": current_hour
                    })
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session tracking if position was closed by SL/TP/Horizon during the session
        # This allows for a new entry in the same session if another setup forms.
        # However, the current strategy uses `self.last_session_entry_bar` to prevent multiple entries per session.
        # If we wanted to allow re-entry after a successful exit, we would reset `self.last_session_entry_bar = -1` here.
        pass