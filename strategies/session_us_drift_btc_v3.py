from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionDriftBTC(Strategy):
    METADATA = {
        "name": "Session Drift BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-3 hours average hold
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13 # UTC 13:00 (9 AM ET)
        self.us_session_end_hour = 21   # UTC 21:00 (5 PM ET) - target exit before this
        self.min_session_move_factor = 1.0 # Multiplier for median_true_range
        self.lookback_period_median_range = 24 # For median true range
        self.entry_cooldown_bars = 4 # Cooldown after an entry attempt
        self.last_entry_bar = -self.entry_cooldown_bars
        self.entry_triggered_today = False # To ensure only one entry per day based on session logic

        # For the "faster trigger variant" - using MACD crossover
        self.macd_fast_period = 12
        self.macd_slow_period = 26
        self.macd_signal_period = 9

        # State to track MACD cross for transition detection
        self.last_macd_signal = None
        self.last_macd_line = None


    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if not trs:
            return None
        return sum(trs[-period:]) / period

    def _median_true_range(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if not trs:
            return None
        sorted_trs = sorted(trs[-period:])
        mid = len(sorted_trs) // 2
        if len(sorted_trs) % 2 == 0:
            return (sorted_trs[mid - 1] + sorted_trs[mid]) / 2
        else:
            return sorted_trs[mid]

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _macd(self, closes, fast, slow, signal):
        if len(closes) < slow + signal:
            return None
        fast_ema = self._ema(closes, fast)
        slow_ema = self._ema(closes, slow)
        if fast_ema is None or slow_ema is None:
            return None, None, None
        macd_line = fast_ema - slow_ema

        macd_vals = []
        # Recompute MACD values for signal line calculation
        for i in range(slow - 1, len(closes)):
            f = self._ema(closes[:i+1], fast)
            s = self._ema(closes[:i+1], slow)
            if f is not None and s is not None:
                macd_vals.append(f - s)

        if len(macd_vals) < signal:
            return macd_line, macd_line, 0.0 # Not enough for signal line yet, use macd_line as signal
        
        signal_line = self._ema(macd_vals, signal)
        if signal_line is None:
            return macd_line, macd_line, 0.0 # Fallback
            
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram


    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset daily entry flag at the start of a new day (e.g., UTC 00:00)
        if ctx.bar_index > self.METADATA["warmup_bars"] and current_hour == 0 and not self.entry_triggered_today:
            self.entry_triggered_today = False # Reset for the new day
        elif self.entry_triggered_today and current_hour != 0:
            # If it's a new day and we were triggered, reset
            if ctx.bar.timestamp.day != (ctx.bar.timestamp - ctx.bar.timedelta).day:
                self.entry_triggered_today = False

        # --- Exit Logic ---
        if ctx.has_position():
            # Exit before US session ends if still in position
            if ctx.position_direction() == "long" and current_hour >= self.us_session_end_hour:
                 return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "time_based_exit_end_of_us_session",
                    "current_hour": current_hour,
                    "entry_price": ctx.position["entry_price"]
                })
            # For short positions, exit also around end of US session to avoid holding through next session potentially
            if ctx.position_direction() == "short" and current_hour >= self.us_session_end_hour:
                 return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "time_based_exit_end_of_us_session",
                    "current_hour": current_hour,
                    "entry_price": ctx.position["entry_price"]
                })


        # --- Entry Cooldown ---
        if ctx.bar_index - self.last_entry_bar < self.entry_cooldown_bars:
            return None

        # --- Main Entry Logic: Session Drift ---
        median_tr = self._median_true_range(highs, lows, closes, self.lookback_period_median_range)
        if median_tr is None:
            return None

        # Check for US session start for potential entry
        if current_hour == self.us_session_start_hour and not self.entry_triggered_today:
            # Calculate the move during the previous bar (end of prior session)
            prev_close = closes[-2]
            current_close = closes[-1]
            move = current_close - prev_close
            move_percent = (move / prev_close) * 100

            # Condition: decisive move, but not a single-bar spike
            # A "decisive move" is one that exceeds a certain multiple of the median true range
            # And it should not be a huge single bar spike (e.g., current bar range vs median_tr)
            
            # Use price change in USD for absolute move
            required_move_usd = median_tr * self.min_session_move_factor
            current_bar_range = highs[-1] - lows[-1]

            # Check if the previous bar had a significant move
            if abs(move) > required_move_usd and current_bar_range < (median_tr * 3): # Avoid very large single bar spikes
                # If the prior bar was bullish and closed strong
                if move > 0: # Bullish move
                    self.last_entry_bar = ctx.bar_index
                    self.entry_triggered_today = True
                    return ctx.signal("long", confidence=0.75, metadata={
                        "reason": "us_session_drift_bullish",
                        "session_start_hour": current_hour,
                        "move_usd": move,
                        "move_percent": move_percent,
                        "median_true_range": median_tr,
                        "current_bar_range": current_bar_range
                    })
                # If the prior bar was bearish and closed strong
                elif move < 0: # Bearish move
                    self.last_entry_bar = ctx.bar_index
                    self.entry_triggered_today = True
                    return ctx.signal("short", confidence=0.75, metadata={
                        "reason": "us_session_drift_bearish",
                        "session_start_hour": current_hour,
                        "move_usd": move,
                        "move_percent": move_percent,
                        "median_true_range": median_tr,
                        "current_bar_range": current_bar_range
                    })
        
        # --- Faster Trigger Variant: MACD Crossover (requires transition detection) ---
        macd_line, signal_line, histogram = self._macd(closes, self.macd_fast_period, self.macd_slow_period, self.macd_signal_period)

        if macd_line is None or signal_line is None:
            return None

        # Detect MACD crossover for entry
        if self.last_macd_signal is not None and self.last_macd_line is not None:
            # Bullish crossover
            if macd_line > signal_line and self.last_macd_line <= self.last_macd_signal:
                if not ctx.has_position() and ctx.bar_index - self.last_entry_bar >= self.entry_cooldown_bars:
                    self.last_entry_bar = ctx.bar_index
                    return ctx.signal("long", confidence=0.6, metadata={
                        "reason": "macd_bullish_crossover",
                        "macd_line": macd_line,
                        "signal_line": signal_line,
                        "histogram": histogram
                    })
            # Bearish crossover
            elif macd_line < signal_line and self.last_macd_line >= self.last_macd_signal:
                if not ctx.has_position() and ctx.bar_index - self.last_entry_bar >= self.entry_cooldown_bars:
                    self.last_entry_bar = ctx.bar_index
                    return ctx.signal("short", confidence=0.6, metadata={
                        "reason": "macd_bearish_crossover",
                        "macd_line": macd_line,
                        "signal_line": signal_line,
                        "histogram": histogram
                    })
        
        self.last_macd_line = macd_line
        self.last_macd_signal = signal_line

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for debugging/analysis
        pass