from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcSessionBreakout(Strategy):
    METADATA = {
        "name": "BTC Session Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 48,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 7  # UTC 7:00 (European morning start)
        self.session_end_hour = 12   # UTC 12:00 (European morning end)
        self.exit_hour = 12         # Exit by UTC 12:00 if still open
        self.range_period = 24       # Period for median range calculation
        self.min_move_multiplier = 1.0 # Multiplier for median range to define decisive move
        self.spike_threshold = 0.6   # Max % of move allowed in single bar
        self.cooldown_bars = 6       # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _median(self, data):
        if not data:
            return 0
        sorted_data = sorted(data)
        mid = len(sorted_data) // 2
        if len(sorted_data) % 2 == 0:
            return (sorted_data[mid - 1] + sorted_data[mid]) / 2
        else:
            return sorted_data[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period + 1)
        highs = ctx.highs(self.range_period + 1)
        lows = ctx.lows(self.range_period + 1)

        if len(closes) < self.range_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: close position by session_end_hour
        if ctx.has_position() and current_hour >= self.exit_hour:
            return ctx.signal("flat", confidence=0.6, metadata={
                "reason": "session_end_exit",
                "current_hour": current_hour,
                "entry_hour": ctx.position["metadata"].get("entry_hour", "N/A"),
            })

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Session entry logic
        if current_hour == self.session_end_hour: # Signal at the close of the session
            # Calculate daily ranges for the last 'range_period' days
            daily_ranges = []
            for i in range(self.range_period):
                # Approximate daily range from 24 1-hour bars
                # This is a simplification; a more robust approach would track true daily ranges
                day_high = max(highs[-(i+24):-i]) if i < self.range_period-24 else max(highs[-24:])
                day_low = min(lows[-(i+24):-i]) if i < self.range_period-24 else min(lows[-24:])
                daily_ranges.append(day_high - day_low)
            
            median_daily_range = self._median(daily_ranges)
            
            if median_daily_range == 0:
                return None

            # Calculate session move (from session_start_hour to session_end_hour)
            # Find the index corresponding to session_start_hour in the current bar's context
            # This assumes bars are ordered by time and we can go back 'session_end_hour - session_start_hour' bars
            session_bars_count = self.session_end_hour - self.session_start_hour
            if len(closes) < session_bars_count + 1:
                return None

            session_open_price = ctx.opens(session_bars_count + 1)[0]
            session_close_price = ctx.bar.close
            session_move = session_close_price - session_open_price
            session_move_abs = abs(session_move)
            
            # Check for decisive move
            min_decisive_move = median_daily_range * self.min_move_multiplier
            if session_move_abs < min_decisive_move:
                return None

            # Check for single-bar spike (if the last bar's move dominates the session move)
            last_bar_move = abs(ctx.bar.close - ctx.bar.open)
            if last_bar_move / session_move_abs > self.spike_threshold:
                return None

            confidence = min(0.5 + (session_move_abs / (2 * median_daily_range)), 0.9)
            
            if not ctx.has_position():
                if session_move > 0: # Bullish session
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("long", confidence=confidence, metadata={
                        "reason": "bullish_session_breakout",
                        "session_move": session_move,
                        "median_daily_range": median_daily_range,
                        "min_decisive_move": min_decisive_move,
                        "current_hour": current_hour,
                        "entry_hour": current_hour,
                    })
                elif session_move < 0: # Bearish session
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("short", confidence=confidence, metadata={
                        "reason": "bearish_session_breakout",
                        "session_move": session_move,
                        "median_daily_range": median_daily_range,
                        "min_decisive_move": min_decisive_move,
                        "current_hour": current_hour,
                        "entry_hour": current_hour,
                    })
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Record the last trade bar to enforce cooldown
        self.last_trade_bar = ctx.bar_index
        pass