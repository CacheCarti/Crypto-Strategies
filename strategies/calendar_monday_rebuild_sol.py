from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondayRebuild(Strategy):
    METADATA = {
        "name": "SOL Monday Rebuild",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # Aim for a few hours hold
        "warmup_bars": 72, # Sufficient for 72-period calculations
        "required_features": [], # No external features needed for this calendar strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.weekend_period = 48 # 2 days * 24 hours
        self.confirmation_period = 4 # 4 hours for Monday confirmation
        self.cooldown_bars = 48 # 2 days cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade if conditions met

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        opens = ctx.opens(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check if it's Monday
        if ctx.bar.timestamp.weekday() != 0: # Monday is 0
            return None

        # Check if it's within US trading hours (e.g., 13 UTC to 21 UTC)
        # 13 UTC is 9 AM ET, 21 UTC is 5 PM ET
        if not (13 <= ctx.bar.timestamp.hour < 21):
            return None

        # Calculate weekend move (Friday close to Sunday close)
        # Friday close: ctx.closes(self.weekend_period + 24) for Friday 17:00 UTC if weekend_period is 48
        # This is tricky with exact bar indices. Let's simplify to a price change over the past `weekend_period`
        # and then confirm direction on Monday.

        # Let's define the "weekend move" as the price change from Friday 17:00 UTC to Sunday 17:00 UTC
        # This is a bit complex to get exact times. A simpler approach:
        # Check price change over the last `weekend_period` (48 hours)
        # This covers roughly Friday close to Sunday close if the current bar is Monday morning.

        # To be more precise, let's look at the price difference from the close 48 bars ago
        # (which would be roughly Friday close if current is Sunday close)
        # to the close 4 bars ago (Sunday close if confirmation period is 4 hours)
        # This requires more historical data than just `warmup_bars`.
        # Let's simplify: compare current price to price 48 bars ago.
        # This is a rolling "weekend" for simplicity in backtest.

        # Ensure enough data for the full weekend period
        if len(closes) < self.weekend_period + self.confirmation_period:
            return None

        # Price before the "weekend" (roughly Friday close)
        price_before_weekend = closes[-(self.weekend_period + self.confirmation_period)]
        # Price at the "end of weekend" (roughly Sunday close)
        price_end_weekend = closes[-(self.confirmation_period + 1)] # one bar before the confirmation starts

        weekend_change = (price_end_weekend - price_before_weekend) / price_before_weekend

        # Calculate confirmation over the last `confirmation_period` bars on Monday
        confirmation_closes = closes[-self.confirmation_period:]
        confirmation_opens = opens[-self.confirmation_period:]

        if len(confirmation_closes) < self.confirmation_period:
            return None

        # Check confirmation direction
        first_confirmation_open = confirmation_opens[0]
        last_confirmation_close = confirmation_closes[-1]
        confirmation_move = (last_confirmation_close - first_confirmation_open) / first_confirmation_open

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Long scenario: Weekend drop, Monday rebuild (upward confirmation)
        if weekend_change < -0.005: # Weekend drop of at least 0.5%
            if confirmation_move > 0.002: # Monday confirms upward move of at least 0.2%
                signal_direction = "long"
                confidence = min(0.7 + abs(confirmation_move * 10), 0.9) # Higher confirmation, higher confidence
                reason = "monday_rebuild_long"
        # Short scenario: Weekend pump, Monday rebuild (downward confirmation)
        elif weekend_change > 0.005: # Weekend pump of at least 0.5%
            if confirmation_move < -0.002: # Monday confirms downward move of at least 0.2%
                signal_direction = "short"
                confidence = min(0.7 + abs(confirmation_move * 10), 0.9) # Higher confirmation, higher confidence
                reason = "monday_rebuild_short"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "weekend_change_pct": weekend_change * 100,
                    "monday_confirmation_pct": confirmation_move * 100,
                    "current_price": ctx.bar.close,
                    "bar_time": ctx.bar.timestamp.isoformat()
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic needed for this strategy, just log for now
        pass