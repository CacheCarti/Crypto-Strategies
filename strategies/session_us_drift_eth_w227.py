from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionBreakout(Strategy):
    METADATA = {
        "name": "US Session Breakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2,  # Intended for 2-hour hold max
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24  # For median range calculation (24 hours)
        self.session_hours = list(range(13, 21))  # US session: UTC 13:00 to 20:59
        self.last_trade_bar = -1
        self.cooldown_bars = 5
        self.min_session_move_factor = 1.0  # Percentage of median range
        self.max_session_move_factor = 2.5 # Avoid exhausted moves

    def _median(self, values):
        if not values:
            return 0
        sorted_values = sorted(values)
        mid = len(sorted_values) // 2
        if len(sorted_values) % 2 == 0:
            return (sorted_values[mid - 1] + sorted_values[mid]) / 2
        else:
            return sorted_values[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.lookback_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit existing position before end of US session (21:00 UTC)
        if ctx.has_position() and current_hour == 20: # Exit on 20:00 bar, before 21:00
            if ctx.position["direction"] == "long":
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close_exit", "exit_hour": current_hour, "current_price": ctx.bar.close})
            elif ctx.position["direction"] == "short":
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close_exit", "exit_hour": current_hour, "current_price": ctx.bar.close})

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Logic for US session end (20:00 UTC bar, capturing 19:00-20:00 range)
        if current_hour == 20: # This bar captures the last hour of the session (19:00-20:00)
            # Calculate daily ranges for median
            daily_ranges = [h - l for h, l in zip(highs[:-1], lows[:-1])]
            median_daily_range = self._median(daily_ranges)

            if median_daily_range == 0:
                return None

            # Find min and max price during the US session (13:00 to 20:00)
            session_high = -math.inf
            session_low = math.inf
            session_open_price = None

            # Iterate back through bars to find session high/low/open
            for i in range(1, len(ctx.bar_history) + 1):
                past_bar = ctx.bar_history[-i]
                past_hour = past_bar.timestamp.hour
                if past_hour not in self.session_hours:
                    break # Out of session, stop looking back

                session_high = max(session_high, past_bar.high)
                session_low = min(session_low, past_bar.low)
                if past_hour == self.session_hours[0]: # This is the first bar of the session
                    session_open_price = past_bar.open

            if session_open_price is None: # Not enough session data yet
                return None

            session_range = session_high - session_low
            session_move = ctx.bar.close - session_open_price

            # Check if session move is decisive but not exhausted
            min_decisive_move = median_daily_range * self.min_session_move_factor / 100
            max_decisive_move = median_daily_range * self.max_session_move_factor / 100

            if abs(session_move) > min_decisive_move and abs(session_move) < max_decisive_move:
                confidence = min(0.9, abs(session_move) / (median_daily_range * 0.01) * 0.05) # Scale confidence by move size

                if session_move > 0: # Bullish session, look for long next bar
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("long", confidence=confidence, metadata={
                            "reason": "us_session_bullish_breakout",
                            "session_move_pct": session_move / session_open_price * 100,
                            "median_daily_range_pct": median_daily_range / session_open_price * 100,
                            "current_price": ctx.bar.close,
                            "session_high": session_high,
                            "session_low": session_low,
                        })
                elif session_move < 0: # Bearish session, look for short next bar
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("short", confidence=confidence, metadata={
                            "reason": "us_session_bearish_breakdown",
                            "session_move_pct": session_move / session_open_price * 100,
                            "median_daily_range_pct": median_daily_range / session_open_price * 100,
                            "current_price": ctx.bar.close,
                            "session_high": session_high,
                            "session_low": session_low,
                        })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just reset cooldown if needed
        # self.last_trade_bar = ctx.bar_index # This is already handled by the signal return
        pass