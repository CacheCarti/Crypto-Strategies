from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendPositioningSOL(Strategy):
    METADATA = {
        "name": "Weekend Positioning SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 12,
        "required_features": [], # No specific features required for this calendar strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars * 2 # Initialize to allow immediate trading

        # Parameters for decisive move detection
        self.decisive_move_period = 6 # Look at last 6 hours
        self.decisive_move_threshold_pct = 0.5 # 0.5% move over 6 hours

        # For position tracking to ensure only one trade per event
        self.traded_this_weekend_cycle = False

    def _calculate_price_change_pct(self, closes, period):
        if len(closes) < period:
            return 0.0
        return ((closes[-1] - closes[-period]) / closes[-period]) * 100.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.decisive_move_period + 1)
        if len(closes) < self.decisive_move_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # --- Entry Logic: Late Friday positioning ---
        # Target: Friday 20:00 UTC to Friday 23:00 UTC (hours 20, 21, 22)
        # This gives a window to capture late Friday positioning
        if current_weekday == 4 and 20 <= current_hour <= 22 and not ctx.has_position():
            if not self.traded_this_weekend_cycle: # Only one trade per cycle
                # Check for decisive move in the last 6 hours
                price_change_pct = self._calculate_price_change_pct(closes, self.decisive_move_period)

                if price_change_pct > self.decisive_move_threshold_pct: # Decisive upward move
                    self.traded_this_weekend_cycle = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "friday_late_positioning_bullish",
                            "price_change_pct_6h": round(price_change_pct, 2),
                            "current_hour": current_hour,
                            "current_weekday": current_weekday,
                        }
                    )
                elif price_change_pct < -self.decisive_move_threshold_pct: # Decisive downward move
                    self.traded_this_weekend_cycle = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "friday_late_positioning_bearish",
                            "price_change_pct_6h": round(price_change_pct, 2),
                            "current_hour": current_hour,
                            "current_weekday": current_weekday,
                        }
                    )
        
        # --- Reset for new weekend cycle ---
        # Reset the traded flag on Sunday 00:00 UTC to allow trading next Friday
        if current_weekday == 6 and current_hour == 0:
            self.traded_this_weekend_cycle = False

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Update last trade bar on close to enforce cooldown
        self.last_trade_bar = ctx.bar_index
        # No specific adaptive logic needed for this strategy, just logging
        ctx.log(f"Position closed: {position['direction']} from {position['entry_price']} to {position['exit_price']} (Return: {position['return_bps']} bps, Held: {position['bars_held']} bars)")