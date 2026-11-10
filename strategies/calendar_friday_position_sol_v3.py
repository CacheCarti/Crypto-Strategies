from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolFridayPositioning(Strategy):
    METADATA = {
        "name": "SOL Friday Positioning",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2-3 hour hold, but can vary
        "warmup_bars": 24, # Need enough bars for 6-hour decisive move check
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_signal_bar_index = -1
        self.cooldown_bars = 12 # Wait 12 bars (12 hours) after a trade
        self.last_friday_signal_timestamp = None # To prevent repeated signals on the same Friday
        self.min_decisive_move_pct = 0.5 # 0.5% move over 6 hours

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # Check if it's Friday, late UTC (around 18:00 - 23:00 UTC)
        # and not too close to Saturday UTC start (00:00 UTC)
        is_friday = ctx.bar.timestamp.weekday() == 4 # Friday is 4
        current_hour = ctx.bar.timestamp.hour

        # Define the window for "late Friday positioning"
        # Let's say 18:00 UTC to 23:00 UTC on Friday
        # This gives a 6-hour window
        if not (is_friday and current_hour >= 18 and current_hour <= 23):
            return None

        # Prevent multiple signals on the same Friday
        if self.last_friday_signal_timestamp is not None:
            # If the last signal was on the same Friday (same day, same year)
            if (self.last_friday_signal_timestamp.year == ctx.bar.timestamp.year and
                self.last_friday_signal_timestamp.month == ctx.bar.timestamp.month and
                self.last_friday_signal_timestamp.day == ctx.bar.timestamp.day):
                return None # Already signaled this Friday

        # Check for decisive move over the last 6 hours
        # We need at least 6 bars for this
        if len(closes) < 6:
            return None

        # Calculate the percentage change over the last 6 bars (6 hours)
        six_hour_ago_close = closes[-6]
        current_close = ctx.bar.close
        move_pct = ((current_close - six_hour_ago_close) / six_hour_ago_close) * 100

        # Determine "decisive" move
        # If the move is positive and exceeds threshold, consider long
        # If the move is negative and exceeds threshold, consider short
        if ctx.has_position():
            return None

        signal_direction = None
        reason = ""
        confidence = 0.0

        if move_pct >= self.min_decisive_move_pct:
            signal_direction = "long"
            reason = "friday_late_utc_decisive_bull_move"
            confidence = min(1.0, move_pct / (self.min_decisive_move_pct * 2)) # Scale confidence up to 2x threshold
        elif move_pct <= -self.min_decisive_move_pct:
            signal_direction = "short"
            reason = "friday_late_utc_decisive_bear_move"
            confidence = min(1.0, abs(move_pct) / (self.min_decisive_move_pct * 2))

        if signal_direction:
            self.last_signal_bar_index = ctx.bar_index
            self.last_friday_signal_timestamp = ctx.bar.timestamp
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "move_pct_6h": round(move_pct, 2),
                    "current_close": round(current_close, 2),
                    "six_hour_ago_close": round(six_hour_ago_close, 2),
                    "timestamp": ctx.bar.timestamp.isoformat()
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details for analysis
        pass