from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanSessionBreakout(Strategy):
    METADATA = {
        "name": "European Session Breakout SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Intended to hold for up to 3 hours or end of session
        "warmup_bars": 50,
        "required_features": [], # No specific features required beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7 AM
        self.entry_hour_end = 12   # UTC 12 PM
        self.exit_hour = 12        # UTC 12 PM
        self.min_range_multiplier = 1.5 # How much the move should exceed recent median range
        self.cooldown_bars = 5     # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.session_traded_today = False
        self.range_period = 20     # Period for median range calculation

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return 0.0
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        sorted_ranges = sorted(ranges)
        mid = len(sorted_ranges) // 2
        return (sorted_ranges[mid] + sorted_ranges[~mid]) / 2 # Handles even/odd length

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset session_traded_today flag at the start of a new day (UTC 00:00)
        if current_hour == 0 and not self.session_traded_today:
            self.session_traded_today = False

        # --- Position Management ---
        if ctx.has_position():
            # Exit at the end of the European session or if held too long
            if current_hour >= self.exit_hour and ctx.bar_index > self.last_trade_bar + 1:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

            # If we have a position, just hold unless it's time to exit
            return ctx.signal("hold", confidence=0.0)

        # --- Entry Logic ---
        # Cooldown period check
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Only consider entry during the defined European session hours and if not traded today
        if self.entry_hour_start <= current_hour < self.entry_hour_end and not self.session_traded_today:
            # Calculate recent median range
            median_range = self._median_range(highs[:-1], lows[:-1], self.range_period) # Exclude current bar for range calc

            if median_range == 0: # Avoid division by zero
                return None

            # Calculate current bar's move
            current_move = closes[-1] - opens[-1]
            current_bar_range = highs[-1] - lows[-1]

            # Check for decisive move (exceeds median range by multiplier)
            # And prevent trading on single-bar spikes (move too close to bar range)
            if abs(current_move) > median_range * self.min_range_multiplier and \
               abs(current_move) < 0.8 * current_bar_range: # Ensure it's not mostly just a wick/single spike

                signal_direction = None
                if current_move > 0: # Bullish move
                    signal_direction = "long"
                elif current_move < 0: # Bearish move
                    signal_direction = "short"

                if signal_direction:
                    self.last_trade_bar = ctx.bar_index
                    self.session_traded_today = True # Mark that we've traded in this session/day
                    return ctx.signal(
                        signal_direction,
                        confidence=0.8,
                        metadata={
                            "reason": f"european_session_breakout_{signal_direction}",
                            "current_hour": current_hour,
                            "current_move": current_move,
                            "median_range": median_range,
                            "min_range_multiplier": self.min_range_multiplier,
                            "price_at_signal": closes[-1]
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session_traded_today to allow re-entry if strategy permits multiple entries per session,
        # but current logic explicitly sets it to true on entry, preventing multiple entries.
        # This can be used for adaptive logic if needed.
        pass