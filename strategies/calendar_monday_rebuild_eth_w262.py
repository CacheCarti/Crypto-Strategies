from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendReversalMondayOpen(Strategy):
    METADATA = {
        "name": "Weekend Reversal Monday Open",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # ~2 hours, allowing for Monday open confirmation
        "warmup_bars": 72, # Enough for 3 full days of 1h bars
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.weekend_move = 0.0
        self.last_trade_bar = -99999 # Initialize far in the past to allow first trade
        self.cooldown_bars = 48 # 2 days cooldown after a Monday trade attempt

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(72) # Last 3 days of 1-hour closes
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # --- Weekend Move Calculation ---
        # Calculate weekend move on Sunday evening (UTC) for Monday's decision
        if current_weekday == 6 and current_hour == 23: # Sunday 23:00 UTC
            # Find Friday 17:00 UTC close (start of weekend for US)
            friday_17_utc_bar_index = -1
            for i in range(len(closes) - 1, -1, -1):
                timestamp = ctx.bar.timestamp - (ctx.bar_index - (len(closes) - 1 - i)) * (ctx.bar.timestamp - ctx.closes.timestamp(-2)) # Approximate timestamp
                # This is a proxy. In a real system, we'd iterate through historical timestamps.
                # For contract, we'll simplify and just look back a fixed number of bars if we can't get exact timestamp.
                # Let's assume Friday 17:00 UTC is roughly 54 bars ago (24*2 + 6 hours) if current is Sunday 23:00 UTC
                if len(closes) > 54: # Ensure enough history
                    friday_close = closes[-54] # Friday 17:00 UTC (approx)
                    self.weekend_move = (ctx.bar.close - friday_close) / friday_close
                    # print(f"Sunday 23:00 UTC: Calculated weekend_move: {self.weekend_move:.4f}")
                    break
        
        # --- Monday Trading Logic ---
        # Trade only if we haven't traded recently
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Execute on Monday during US market open hours (13:00 - 17:00 UTC)
        if current_weekday == 0 and 13 <= current_hour <= 17: # Monday 13:00 to 17:00 UTC
            if ctx.has_position():
                return None # Already in a position

            # Check for a significant weekend move
            if abs(self.weekend_move) < 0.01: # Less than 1% move, not significant enough
                return None

            # Confirm direction with current bar momentum (simple close vs open)
            # Or with a short-term EMA cross, or RSI... let's use recent price action
            # The "first US hours confirm" means current bar close vs open, or recent trend
            
            # Use a simple 3-bar SMA for short-term trend confirmation
            if len(closes) < 3:
                return None
            
            sma_3 = sum(closes[-3:]) / 3
            
            signal_direction = None
            reason = ""
            confidence = 0.0

            if self.weekend_move > 0.01: # Weekend was bullish
                # Look for Monday confirmation to go long: price above short SMA
                if ctx.bar.close > sma_3 and ctx.bar.close > ctx.bar.open:
                    signal_direction = "long"
                    reason = "monday_open_bullish_confirmation_after_weekend_up"
                    confidence = min(0.5 + abs(self.weekend_move) * 5, 0.9) # Scale with move size
            elif self.weekend_move < -0.01: # Weekend was bearish
                # Look for Monday confirmation to go short: price below short SMA
                if ctx.bar.close < sma_3 and ctx.bar.close < ctx.bar.open:
                    signal_direction = "short"
                    reason = "monday_open_bearish_confirmation_after_weekend_down"
                    confidence = min(0.5 + abs(self.weekend_move) * 5, 0.9) # Scale with move size

            if signal_direction:
                self.last_trade_bar = ctx.bar_index # Mark the trade bar
                # Reset weekend_move to prevent re-triggering on subsequent Monday bars
                self.weekend_move = 0.0 
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "weekend_move_pct": self.weekend_move * 100,
                        "current_close": ctx.bar.close,
                        "sma_3": sma_3,
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this calendar strategy
        pass