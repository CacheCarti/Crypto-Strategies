from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class SessionTrendFollower(Strategy):
    METADATA = {
        "name": "Session Trend Follower",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for a 6-hour hold
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 18 # UTC 18:00 (late US session start)
        self.session_end_hour = 23 # UTC 23:00 (end of late US session)
        self.entry_threshold_multiplier = 1.5 # How many times the median range to consider a decisive move
        self.min_session_bars = 4 # Minimum bars for a valid session move
        self.cooldown_bars = 5 # Bars to wait after an exit before considering a new entry
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        self.median_range_period = 20 # Period for median daily range calculation
        self.atr_period = 14 # Period for ATR for stop placement
        self.atr_multiplier = 2.0 # Multiplier for ATR based stop loss

        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open_price = None
        self.session_bar_count = 0
        self.session_direction = None # "long" or "short"
        self.session_move_magnitude = 0.0

    def _get_daily_ranges(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = []
        for i in range(len(highs) - period, len(highs)):
            ranges.append(highs[i] - lows[i])
        return ranges

    def _median(self, values):
        if not values:
            return 0.0
        sorted_values = sorted(values)
        mid = len(sorted_values) // 2
        if len(sorted_values) % 2 == 0:
            return (sorted_values[mid - 1] + sorted_values[mid]) / 2
        else:
            return sorted_values[mid]

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        # Simple moving average of True Ranges
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period


    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        opens = ctx.opens(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Calculate median daily range for entry threshold
        daily_ranges = self._get_daily_ranges(highs, lows, self.median_range_period)
        if daily_ranges is None:
            return None
        median_daily_range = self._median(daily_ranges)
        if median_daily_range == 0:
            median_daily_range = 0.01 # Avoid division by zero

        # Calculate ATR for stop loss
        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        # Session logic
        is_session_start_bar = (current_hour == self.session_start_hour)
        is_session_end_bar = (current_hour == self.session_end_hour)

        if is_session_start_bar and self.session_bar_count == 0:
            # New session starts
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_open_price = ctx.bar.open
            self.session_bar_count = 1
            self.session_direction = None
            self.session_move_magnitude = 0.0
        elif self.session_bar_count > 0:
            # Continue tracking session
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_bar_count += 1

            # Determine session direction and magnitude
            if self.session_open_price is not None:
                if current_price > self.session_open_price:
                    self.session_direction = "long"
                    self.session_move_magnitude = current_price - self.session_open_price
                elif current_price < self.session_open_price:
                    self.session_direction = "short"
                    self.session_move_magnitude = self.session_open_price - current_price

        # Exit existing position if session is ending or reversal is detected
        if ctx.has_position():
            if current_hour == 0: # Exit before UTC 0:00 to avoid holding overnight
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_close_time", "hour": current_hour})
            
            # Simple structure stop: if price goes against the session move by significant amount
            # This is a very simple structure stop, could be more advanced
            if ctx.position_direction() == "long":
                if current_price < self.session_open_price - (current_atr * 1.0): # 1 ATR below session open as rough stop
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_structure_stop", "current_price": current_price, "session_open": self.session_open_price, "atr": current_atr})
            elif ctx.position_direction() == "short":
                if current_price > self.session_open_price + (current_atr * 1.0): # 1 ATR above session open as rough stop
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_structure_stop", "current_price": current_price, "session_open": self.session_open_price, "atr": current_atr})

        # Entry logic
        if not ctx.has_position() and (ctx.bar_index - self.last_trade_bar) > self.cooldown_bars:
            if self.session_bar_count >= self.min_session_bars and self.session_open_price is not None:
                required_move = median_daily_range * self.entry_threshold_multiplier

                if self.session_direction == "long" and self.session_move_magnitude >= required_move:
                    # Check for exhaustion: current bar not too far from session high
                    if (self.session_high - current_price) / median_daily_range < 0.2: # price is within 20% of session high
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.0 + (self.session_move_magnitude / required_move) * 0.5, # Scale confidence by how strong the move is
                            metadata={
                                "reason": "session_trend_follow_long",
                                "session_start_hour": self.session_start_hour,
                                "session_open": self.session_open_price,
                                "current_price": current_price,
                                "session_move_magnitude": self.session_move_magnitude,
                                "required_move": required_move,
                                "median_daily_range": median_daily_range,
                            }
                        )
                elif self.session_direction == "short" and self.session_move_magnitude >= required_move:
                    # Check for exhaustion: current bar not too far from session low
                    if (current_price - self.session_low) / median_daily_range < 0.2: # price is within 20% of session low
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.0 + (self.session_move_magnitude / required_move) * 0.5, # Scale confidence
                            metadata={
                                "reason": "session_trend_follow_short",
                                "session_start_hour": self.session_start_hour,
                                "session_open": self.session_open_price,
                                "current_price": current_price,
                                "session_move_magnitude": self.session_move_magnitude,
                                "required_move": required_move,
                                "median_daily_range": median_daily_range,
                            }
                        )
        
        # Reset session tracking at the end of the session window, or if it's outside the session hours
        if is_session_end_bar or (current_hour != self.session_start_hour and self.session_bar_count > 0 and current_hour not in range(self.session_start_hour, self.session_end_hour + 1)):
            self.session_bar_count = 0
            self.session_open_price = None
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_direction = None
            self.session_move_magnitude = 0.0

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after a position closes
        self.last_trade_bar = ctx.bar_index
        # Optional: Adaptive sizing logic could go here based on position["return_bps"]