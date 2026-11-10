from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionDrift(Strategy):
    METADATA = {
        "name": "US Session Drift SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # Aim for 2-hour hold, but can extend to session end
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this price-action based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13  # UTC 13:00
        self.us_session_end_hour = 21    # UTC 21:00 (exit before this)
        self.median_range_period = 20    # Period for calculating median true range
        self.min_session_move_factor = 1.2 # Session move must be 1.2x median range
        self.max_session_move_factor = 3.0 # Session move must not exceed 3.0x median range (avoid exhaustion)
        self.cooldown_bars = 12          # Cooldown for 12 hours after a trade (12 bars * 1h)
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # Store session start price and highest/lowest for the session
        self.session_start_price = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_start_bar_index = -1

    def _true_range(self, high, low, close_prev):
        return max(high - low, abs(high - close_prev), abs(low - close_prev))

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
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # --- Session Management ---
        is_us_session_start = current_hour == self.us_session_start_hour
        is_us_session_end_approach = current_hour == self.us_session_end_hour - 1 # Exit before 21:00

        # Reset session data at the start of US session
        if is_us_session_start and ctx.bar_index > self.session_start_bar_index:
            self.session_start_price = ctx.bar.open # Use open for session start price
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
        elif ctx.bar_index > self.session_start_bar_index: # Update session high/low
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # --- Exit Logic ---
        if ctx.has_position():
            if is_us_session_end_approach and ctx.position_direction() == "long":
                # Exit long before session end
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_exit_session_end_approach", "current_hour": current_hour, "close": current_close})
            elif is_us_session_end_approach and ctx.position_direction() == "short":
                # Exit short before session end
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_exit_session_end_approach", "current_hour": current_hour, "close": current_close})

        # --- Entry Logic ---
        if ctx.has_position() or (ctx.bar_index - self.last_trade_bar_index) < self.cooldown_bars:
            return None # Already in position or in cooldown

        # Only consider entries within the US session, but not at the very start or end
        if not (self.us_session_start_hour <= current_hour < self.us_session_end_hour - 1):
            return None

        # Calculate median true range
        if len(closes) < self.median_range_period + 1:
            return None

        true_ranges = []
        for i in range(1, self.median_range_period + 1):
            tr = self._true_range(highs[-i], lows[-i], closes[-i-1])
            true_ranges.append(tr)
        median_tr = self._median(true_ranges)

        if median_tr == 0: # Avoid division by zero
            return None

        # Calculate session move
        if self.session_start_price is None or self.session_start_bar_index == ctx.bar_index:
            return None # Not enough session data yet

        session_move_abs = abs(current_close - self.session_start_price)
        session_move_ratio = session_move_abs / median_tr

        # Check for decisive but not exhausted move
        is_decisive_move = self.min_session_move_factor <= session_move_ratio <= self.max_session_move_factor

        if is_decisive_move:
            if current_close > self.session_start_price: # Bullish session drift
                # Ensure it's not a single-bar spike by checking the session low
                # A bullish drift should ideally have the current price significantly above the session start
                # and ideally not have hit the session low recently.
                if (current_close > self.session_start_price + median_tr * 0.5) and (ctx.bar.low > self.session_low + median_tr * 0.2):
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("long", confidence=0.65,
                                      metadata={"reason": "us_session_bullish_drift",
                                                "current_hour": current_hour,
                                                "session_start_price": self.session_start_price,
                                                "current_close": current_close,
                                                "session_move_ratio": session_move_ratio,
                                                "median_true_range": median_tr})
            elif current_close < self.session_start_price: # Bearish session drift
                # Ensure it's not a single-bar spike by checking the session high
                # A bearish drift should ideally have the current price significantly below the session start
                # and ideally not have hit the session high recently.
                if (current_close < self.session_start_price - median_tr * 0.5) and (ctx.bar.high < self.session_high - median_tr * 0.2):
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("short", confidence=0.65,
                                      metadata={"reason": "us_session_bearish_drift",
                                                "current_hour": current_hour,
                                                "session_start_price": self.session_start_price,
                                                "current_close": current_close,
                                                "session_move_ratio": session_move_ratio,
                                                "median_true_range": median_tr})
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session tracking if a trade just closed, to avoid re-entry too quickly within the same session
        self.session_start_price = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_start_bar_index = -1
        # Cooldown is handled in on_bar by self.last_trade_bar_index
        pass