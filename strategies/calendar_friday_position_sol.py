from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolFridayPositioning(Strategy):
    METADATA = {
        "name": "SOL Friday Positioning",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours, allowing positioning to play out
        "warmup_bars": 24, # Enough for 24-hour lookback
        "required_features": [], # No external features needed for this calendar strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 48 # Cooldown for 2 full days after a trade, given the weekly nature
        self.last_trade_bar = -self.cooldown_bars

        # Lookback period for decisive move confirmation (6 hours)
        self.confirmation_period = 6
        # Threshold for decisiveness (e.g., 0.5% move in 6 hours)
        self.decisive_move_threshold_pct = 0.5

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Check cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check for existing position
        if ctx.has_position():
            # If it's a Friday trade and we're holding into Sunday morning, consider exiting
            if ctx.position_direction() == "long" and ctx.bar.timestamp.weekday() == 6 and ctx.bar.timestamp.hour >= 8: # Sunday 08:00 UTC
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "sunday_morning_exit", "current_price": ctx.bar.close})
            if ctx.position_direction() == "short" and ctx.bar.timestamp.weekday() == 6 and ctx.bar.timestamp.hour >= 8: # Sunday 08:00 UTC
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "sunday_morning_exit", "current_price": ctx.bar.close})
            return None

        # --- Entry Logic ---
        # Look for Friday late afternoon UTC (e.g., 16:00 - 20:00 UTC)
        is_friday = ctx.bar.timestamp.weekday() == 4 # Friday
        is_late_friday_utc = 16 <= ctx.bar.timestamp.hour <= 20

        if is_friday and is_late_friday_utc:
            # Need at least self.confirmation_period bars for analysis
            if len(ctx.closes(self.confirmation_period + 1)) < self.confirmation_period + 1:
                return None

            # Check for decisive move in the last 6 hours
            closes_last_6h = ctx.closes(self.confirmation_period + 1)
            open_6h_ago = closes_last_6h[0] # The close from 6 bars ago
            current_close = ctx.bar.close

            price_change_pct = ((current_close - open_6h_ago) / open_6h_ago) * 100

            # Calculate average volume over the last 6 hours
            volumes_last_6h = ctx.volumes(self.confirmation_period)
            if not volumes_last_6h:
                return None
            avg_volume_6h = sum(volumes_last_6h) / len(volumes_last_6h)

            # Confirm decisiveness with a strong move and above-average volume
            is_decisive_long = price_change_pct >= self.decisive_move_threshold_pct
            is_decisive_short = price_change_pct <= -self.decisive_move_threshold_pct

            # Simple volume confirmation: current volume is higher than average of last 6h
            # This is a very basic confirmation, could be refined
            is_volume_confirmed = ctx.bar.volume > avg_volume_6h

            if is_decisive_long and is_volume_confirmed:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "friday_late_positioning_long",
                        "price_change_pct_6h": round(price_change_pct, 2),
                        "current_volume": ctx.bar.volume,
                        "avg_volume_6h": round(avg_volume_6h, 2),
                        "entry_bar_hour": ctx.bar.timestamp.hour,
                        "entry_bar_weekday": ctx.bar.timestamp.weekday()
                    }
                )
            elif is_decisive_short and is_volume_confirmed:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "friday_late_positioning_short",
                        "price_change_pct_6h": round(price_change_pct, 2),
                        "current_volume": ctx.bar.volume,
                        "avg_volume_6h": round(avg_volume_6h, 2),
                        "entry_bar_hour": ctx.bar.timestamp.hour,
                        "entry_bar_weekday": ctx.bar.timestamp.weekday()
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to apply cooldown after any close
        self.last_trade_bar = ctx.bar_index