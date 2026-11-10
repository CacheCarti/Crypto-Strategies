from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionDriftSOL(Strategy):
    METADATA = {
        "name": "SessionDriftSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 48, # Need enough bars for median range (24 bars for 24h)
    }

    def initialize(self, ctx: BarContext) -> None:
        self.max_lookback = 48 # For median range calculation, 2 days of 1h bars
        self.min_move_factor = 0.5 # Percentage of median range for decisive move
        self.max_spike_factor = 0.5 # Max portion of move that can be a single bar
        self.entry_cooldown_bars = 6 # Cooldown after any trade
        self.last_trade_bar_index = -self.entry_cooldown_bars
        self.session_traded_today = False
        self.last_day_reset = -1

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        sorted_ranges = sorted(ranges)
        mid = len(sorted_ranges) // 2
        if len(sorted_ranges) % 2 == 0:
            return (sorted_ranges[mid - 1] + sorted_ranges[mid]) / 2
        else:
            return sorted_ranges[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.max_lookback)
        opens = ctx.opens(self.max_lookback)
        highs = ctx.highs(self.max_lookback)
        lows = ctx.lows(self.max_lookback)

        if len(closes) < self.max_lookback:
            return None

        # Reset daily trade flag at UTC 00:00
        if ctx.bar.timestamp.hour == 0 and ctx.bar_index != self.last_day_reset:
            self.session_traded_today = False
            self.last_day_reset = ctx.bar_index

        current_hour = ctx.bar.timestamp.hour

        # Exit existing position before UTC 0 or if conditions change
        if ctx.has_position():
            # Exit if it's the end of the trading day (UTC 00:00)
            if current_hour == 0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "eod_exit", "current_hour": current_hour})

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.entry_cooldown_bars:
            return None

        # Only consider entries during late US session (UTC 18-23)
        if not (18 <= current_hour <= 23):
            return None

        # Only one trade per session/day
        if self.session_traded_today:
            return None

        # Calculate median daily range for the last 24 hours
        median_daily_range = self._median_range(highs, lows, 24) # Using 24 1h bars as a proxy for daily range
        if median_daily_range is None or median_daily_range == 0:
            return None

        # Define session start (UTC 18:00) price
        session_start_price = None
        session_bars = []
        for i in range(len(opens) - 1, -1, -1):
            bar_time = ctx.bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0) \
                       + (ctx.bar_index - (len(opens) - 1 - i)) * ctx.bar.timestamp.resolution
            # This bar_time logic needs to be careful. Better to iterate backwards from current bar's timestamp.
            # Simpler: find the open price of the bar at 18:00 UTC *today* or *yesterday* if current hour is past 18.

        # Find the open price of the 18:00 UTC bar for the current day
        # This is a bit tricky with only current bar context. We need to look back to the 18:00 bar.
        # Approximation: find the open price N bars ago that corresponds to 18:00
        # If current_hour is 18, session_start_price is current open.
        # If current_hour is 19, session_start_price is opens[-2] (if current bar is opens[-1])

        # Let's simplify: look at the price change from the 18:00 bar of the *current day*.
        # To do this robustly, we need to find the specific bar.
        # We can't directly query historical timestamps for specific hours.
        # Instead, calculate the move from the *start of the current 18-24 session window*.
        # This means we need the open price of the bar that STARTED at 18:00 UTC.

        # Find the open price of the 18:00 bar
        session_start_bar_index = -1
        for i in range(len(closes) - 1, -1, -1):
            # This is not robust. ctx.bar.timestamp is for the *current* bar.
            # We need the timestamp of historical bars. The contract doesn't expose it.
            # We can only infer relative positions.
            # A simpler approach: look at the move from the close 6 hours ago (if current_hour is 24, from 18).
            # This assumes the current bar is X hours into the session.

            # We need to calculate the session move based on the *current* 18-24 window.
            # Let's say we are at 20:00 UTC. We need the open price at 18:00 UTC.
            # This means the open price of the bar that occurred 2 hours ago.
            # Current bar index is ctx.bar_index. Bar at 18:00 was (ctx.bar_index - (current_hour - 18)) bars ago.
            # So, its open price would be opens[-(current_hour - 18) - 1] if current bar is opens[-1].
            # This gets complex with array indexing.

            # Simpler: Calculate the move over the last (current_hour - 18 + 1) bars.
            # Example: if current_hour is 18, we look at the current bar (1 bar move).
            # If current_hour is 20, we look at the last 3 bars (20-18+1 = 3).
            num_session_bars = current_hour - 18 + 1
            if len(closes) < num_session_bars:
                return None

            session_start_price_approx = opens[-num_session_bars]
            session_current_price = closes[-1]
            session_move = session_current_price - session_start_price_approx
            session_move_pct = (session_move / session_start_price_approx) * 100 if session_start_price_approx != 0 else 0

            # Check if the move is decisive
            min_decisive_move_abs = median_daily_range * self.min_move_factor
            if abs(session_move) < min_decisive_move_abs:
                return None

            # Check for single-bar spike (current bar's move vs total session move)
            current_bar_move = closes[-1] - opens[-1]
            if abs(current_bar_move) / abs(session_move) > (1.0 - self.max_spike_factor):
                return None # Too much of the move is from the current bar

            # Determine direction and signal
            confidence = min(0.5 + (abs(session_move) / (median_daily_range * 1.5)), 0.9) # Scale confidence by move size

            if session_move > 0: # Bullish drift
                if not ctx.has_position() or ctx.position_direction() == "short":
                    self.session_traded_today = True
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("long", confidence=confidence,
                                      metadata={"reason": "late_us_session_drift_long",
                                                "session_move_pct": session_move_pct,
                                                "median_daily_range": median_daily_range,
                                                "current_hour": current_hour})
            elif session_move < 0: # Bearish drift
                if not ctx.has_position() or ctx.position_direction() == "long":
                    self.session_traded_today = True
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("short", confidence=confidence,
                                      metadata={"reason": "late_us_session_drift_short",
                                                "session_move_pct": session_move_pct,
                                                "median_daily_range": median_daily_range,
                                                "current_hour": current_hour})
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We could use this to adapt confidence or other parameters, but for now, just reset cooldown logic.
        pass