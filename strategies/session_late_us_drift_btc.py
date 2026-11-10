from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcLateUsDrift(Strategy):
    METADATA = {
        "name": "BTC Late US Drift",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 3, # ~3 hours
        "warmup_bars": 100,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24 # for median range calculation (24 hours)
        self.entry_threshold_multiplier = 1.5 # 1.5x median range for decisive move
        self.cooldown_bars = 12 # 12 hours cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade
        self.last_entry_day = -1 # To ensure only one entry per day UTC

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        ranges.sort()
        mid = len(ranges) // 2
        if len(ranges) % 2 == 0:
            return (ranges[mid - 1] + ranges[mid]) / 2
        else:
            return ranges[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # If we have a position, check if it's nearing midnight UTC to flat out
        if ctx.has_position():
            # Exit before UTC 0 to avoid holding through session boundaries
            if current_hour == 23 and ctx.position_direction() is not None: # Last hour of the day
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_close_pre_midnight_utc",
                    "current_hour": current_hour,
                    "position_direction": ctx.position_direction(),
                    "entry_price": ctx.position["entry_price"]
                })
            return None # Hold position

        # Entry logic: Late US session (UTC 18-23) for entry signals
        if 18 <= current_hour <= 22 and current_day != self.last_entry_day:
            # Calculate the price change during the current bar
            bar_change = closes[-1] - closes[-2]

            # Calculate the median true range for the lookback period
            median_tr = self._median_range(highs[:-1], lows[:-1], self.lookback_period) # Exclude current bar for range calc
            if median_tr is None or median_tr == 0:
                return None

            # Check for decisive move, but not a single-bar spike
            # We want to see a move that is significant relative to the median range
            # and is not just an extreme single-bar move (which could be noise/exhaustion)
            # For simplicity, we'll check if the current bar's move is "large enough"
            # and then confirm it's not an extreme spike by comparing to the previous bar's body
            
            # Simple decisive move check: current bar's body is significant
            current_bar_body = abs(closes[-1] - closes[-2])
            
            if current_bar_body > median_tr * self.entry_threshold_multiplier:
                # Determine direction of the decisive move
                if bar_change > 0: # Bullish move
                    # Ensure it's not an already exhausted move (e.g., current bar is a reversal candle or very high wick)
                    # For simplicity, we assume if the body is large and positive, it's a follow-through candidate.
                    self.last_trade_bar = ctx.bar_index
                    self.last_entry_day = current_day
                    return ctx.signal("long", confidence=0.75, metadata={
                        "reason": "late_us_session_bullish_drift",
                        "entry_hour": current_hour,
                        "bar_change": bar_change,
                        "median_tr": median_tr,
                        "current_bar_body": current_bar_body,
                        "threshold_multiplier": self.entry_threshold_multiplier
                    })
                elif bar_change < 0: # Bearish move
                    self.last_trade_bar = ctx.bar_index
                    self.last_entry_day = current_day
                    return ctx.signal("short", confidence=0.75, metadata={
                        "reason": "late_us_session_bearish_drift",
                        "entry_hour": current_hour,
                        "bar_change": bar_change,
                        "median_tr": median_tr,
                        "current_bar_body": current_bar_body,
                        "threshold_multiplier": self.entry_threshold_multiplier
                    })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_entry_day on close to allow for a new entry on the same day if circumstances align,
        # but the cooldown still prevents immediate re-entry.
        # This makes the "one entry per day" more robust against early stops.
        self.last_entry_day = -1
        # Update last trade bar to enforce cooldown period
        self.last_trade_bar = ctx.bar_index