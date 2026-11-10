from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightSessionBreakout(Strategy):
    METADATA = {
        "name": "OvernightSessionBreakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 180.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hour hold
        "warmup_bars": 50,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0 # UTC 00:00 to 06:00 is overnight session
        self.session_end_hour = 6
        self.entry_cooldown_bars = 12 # Wait 12 hours after a trade before re-entering
        self.last_signal_bar_index = -self.entry_cooldown_bars * 2 # Initialize far in the past

        self.median_range_period = 20 # For average true range calculation
        self.breakout_threshold_atr_multiplier = 0.8 # How much of ATR move to consider decisive
        self.min_session_candles = 3 # Minimum candles in session to calculate range

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar_index < self.entry_cooldown_bars:
            return None

        # Logic for exiting before the next US session or on structure stop
        if ctx.has_position():
            # Exit around 12:00 UTC (US session open time) if position is still open
            # This is a soft exit; the declared_hold_seconds will act as a hard limit
            if current_hour == 12 and ctx.position_direction() == "long":
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.3, metadata={"reason": "session_close_long_exit", "current_hour": current_hour})
            elif current_hour == 12 and ctx.position_direction() == "short":
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.3, metadata={"reason": "session_close_short_exit", "current_hour": current_hour})
            return None

        # Only look for entry signals at the end of the overnight session
        if current_hour != self.session_end_hour:
            return None

        # Find the start of the overnight session (UTC 00:00)
        session_bars = []
        session_highs = []
        session_lows = []
        session_opens = []
        session_closes = []
        session_volumes = []
        
        # Iterate backwards to find session bars
        for i in range(1, self.warmup_bars + 1):
            bar_timestamp = ctx.bar.timestamp - (i * (ctx.bar.timestamp - (ctx.bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)))) / i
            if bar_timestamp.hour >= self.session_start_hour and bar_timestamp.hour < self.session_end_hour:
                session_bars.append(ctx.bar_index - i)
                session_highs.append(ctx.highs(i+1)[-i-1])
                session_lows.append(ctx.lows(i+1)[-i-1])
                session_opens.append(ctx.opens(i+1)[-i-1])
                session_closes.append(ctx.closes(i+1)[-i-1])
                session_volumes.append(ctx.volumes(i+1)[-i-1])
            elif bar_timestamp.hour == (self.session_start_hour - 1) % 24: # Previous day's end
                break
        
        # Ensure we have enough bars for the session
        if len(session_bars) < self.min_session_candles:
            return None

        session_high = max(session_highs)
        session_low = min(session_lows)
        session_range = session_high - session_low
        
        # Calculate recent average true range
        atr = self._atr(highs, lows, closes, self.median_range_period)
        if atr is None or atr == 0:
            return None

        # Check for decisive move
        decisive_move_threshold = atr * self.breakout_threshold_atr_multiplier

        # Get the closing price of the current bar (end of session)
        current_close = ctx.bar.close

        # Check for breakout direction
        is_bullish_breakout = (current_close > session_high) and (current_close - session_high > decisive_move_threshold)
        is_bearish_breakout = (current_close < session_low) and (session_low - current_close > decisive_move_threshold)

        # Skip single-bar spikes: ensure the session move wasn't just a sudden jump at the very end
        # This is implicitly handled by checking against session_high/low which captures the range
        # but we can add a check if the last bar's move was too extreme compared to the session range
        if is_bullish_breakout:
            # If the last bar's open was already above session high, it's not a breakout from the session
            # or if the last bar's body is too large compared to the session range
            if ctx.bar.open > session_high or (ctx.bar.close - ctx.bar.open) > session_range * 0.5: # 50% of session range
                is_bullish_breakout = False
        
        if is_bearish_breakout:
            if ctx.bar.open < session_low or (ctx.bar.open - ctx.bar.close) > session_range * 0.5:
                is_bearish_breakout = False

        if is_bullish_breakout:
            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                metadata={
                    "reason": "overnight_session_bullish_breakout",
                    "session_high": session_high,
                    "session_low": session_low,
                    "session_range": session_range,
                    "current_close": current_close,
                    "atr": atr,
                    "breakout_threshold": decisive_move_threshold,
                    "entry_hour": current_hour,
                },
            )
        elif is_bearish_breakout:
            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                metadata={
                    "reason": "overnight_session_bearish_breakout",
                    "session_high": session_high,
                    "session_low": session_low,
                    "session_range": session_range,
                    "current_close": current_close,
                    "atr": atr,
                    "breakout_threshold": decisive_move_threshold,
                    "entry_hour": current_hour,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis or adaptive logic
        pass