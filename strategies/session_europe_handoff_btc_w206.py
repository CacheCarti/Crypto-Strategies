from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanMorningBreakout(Strategy):
    METADATA = {
        "name": "European Morning Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for 1-hour session, but can extend slightly
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -self.METADATA["warmup_bars"]
        self.cooldown_bars = 5 # Cooldown after a trade
        self.entry_hour_start = 7  # UTC 7 AM
        self.entry_hour_end = 10 # UTC 10 AM (allow entries until 10 AM)
        self.exit_hour = 12     # UTC 12 PM (exit by this hour)
        self.min_session_range_bps = 50 # Minimum range in bps for a valid session impulse
        self.max_session_range_bps = 300 # Max range to avoid chasing extreme moves
        self.last_signal_direction = None # To prevent re-entering same direction too quickly

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Exit logic: Close by 12 PM UTC, or if a position is held too long
        if ctx.has_position():
            if current_hour >= self.exit_hour and ctx.position["direction"] is not None:
                self.last_trade_bar = ctx.bar_index
                self.last_signal_direction = None # Reset direction lock
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_end_time_exit",
                    "exit_hour": self.exit_hour,
                    "current_hour": current_hour,
                    "entry_price": ctx.position["entry_price"]
                })
            # If position held too long (e.g. 2 hours beyond declared_hold_seconds)
            # This handles cases where the exit_hour is missed or position taken late
            if (ctx.bar_index - self.last_trade_bar) > (self.METADATA["declared_hold_seconds"] / 3600) + 2:
                self.last_trade_bar = ctx.bar_index
                self.last_signal_direction = None
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "position_held_too_long_exit",
                    "bars_held": ctx.bar_index - self.last_trade_bar,
                    "entry_price": ctx.position["entry_price"]
                })

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: European morning session
        if self.entry_hour_start <= current_hour < self.entry_hour_end:
            # Only allow one entry per day per session
            if self.last_trade_bar != -self.METADATA["warmup_bars"] and \
               ctx.bar_index - self.last_trade_bar < (self.entry_hour_end - self.entry_hour_start) * 3600 / 3600: # Check if trade was in same session window
                return None # Already traded in this session

            # Define the start of the current European session (7 AM UTC of the current day)
            session_start_timestamp = ctx.bar.timestamp.replace(hour=self.entry_hour_start, minute=0, second=0, microsecond=0)
            
            # Find bars within the current session up to the current bar
            session_closes = []
            session_highs = []
            session_lows = []
            
            for i in range(len(closes) - 1, -1, -1):
                bar_timestamp = ctx.bar.timestamp - (ctx.bar_index - (len(closes) - 1 - i)) * ctx.bar.interval_seconds
                if bar_timestamp >= session_start_timestamp:
                    session_closes.append(closes[i])
                    session_highs.append(highs[i])
                    session_lows.append(lows[i])
                else:
                    break
            
            session_closes.reverse()
            session_highs.reverse()
            session_lows.reverse()

            if len(session_closes) < 2: # Need at least 2 bars to determine direction
                return None

            session_open_price = session_closes[0] # First close of the session
            current_price = ctx.bar.close

            session_high = max(session_highs)
            session_low = min(session_lows)
            
            # Calculate session range in BPS
            session_range_bps = (session_high - session_low) / session_low * 10000

            if not (self.min_session_range_bps < session_range_bps < self.max_session_range_bps):
                return None # Session not impulsive enough or too impulsive

            # Calculate the impulse direction
            # We look for a clear move from the session open
            
            # Use SMA as a trend filter to confirm direction
            sma_period = 20 # 20-hour SMA for longer term trend
            sma = self._sma(closes, sma_period)
            if sma is None:
                return None

            signal_direction = None
            if current_price > session_open_price and current_price > sma and (current_price - session_open_price) / session_open_price * 10000 > self.min_session_range_bps / 2: # Half of min session range for impulse
                signal_direction = "long"
            elif current_price < session_open_price and current_price < sma and (session_open_price - current_price) / session_open_price * 10000 > self.min_session_range_bps / 2:
                signal_direction = "short"

            if signal_direction and ctx.position_direction() != signal_direction and self.last_signal_direction != signal_direction:
                self.last_trade_bar = ctx.bar_index
                self.last_signal_direction = signal_direction
                return ctx.signal(signal_direction, confidence=0.75, metadata={
                    "reason": f"european_morning_impulse_breakout_{signal_direction}",
                    "current_hour": current_hour,
                    "session_open_price": session_open_price,
                    "current_price": current_price,
                    "session_range_bps": session_range_bps,
                    "sma_20": sma
                })
            
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any daily state if needed, though most state is handled within on_bar
        # For this strategy, the cooldown and last_trade_bar handles daily limits
        pass