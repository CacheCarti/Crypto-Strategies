from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcMondayRebuild(Strategy):
    METADATA = {
        "name": "BTC Monday Rebuild",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 24, # Need at least 24 bars for weekend calc
    }

    def initialize(self, ctx: BarContext) -> None:
        self.weekend_move_threshold_pct = 0.5 # Percentage move over weekend to consider
        self.monday_confirmation_bars = 4 # Number of bars into Monday US session for confirmation
        self.cooldown_bars = 48 # Cooldown for 48 hours (48 bars) after a trade
        self.last_trade_bar_index = -self.cooldown_bars # Initialize to allow trade immediately
        self.last_weekend_processed = -1 # To ensure only one trade per weekend event

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars + 1:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Only process on Monday
        if ctx.bar.timestamp.weekday() != 0: # Monday is 0
            return None

        # Check if we already processed this Monday's event
        if ctx.bar.timestamp.day == self.last_weekend_processed:
            return None

        # Define US session start for confirmation (e.g., 13:00 UTC for NY open)
        # We want to check price action AFTER this time on Monday
        if ctx.bar.timestamp.hour < 13: # Wait until 13:00 UTC (9 AM ET)
            return None

        # Check for confirmation bars
        if ctx.bar.timestamp.hour < 13 + self.monday_confirmation_bars - 1:
            # Not enough confirmation bars yet
            return None

        # Calculate weekend move (Friday close to Sunday close)
        # Need to go back to find Friday close and Sunday close.
        # This is computationally heavy if done perfectly for every bar,
        # so we'll approximate using a fixed lookback and check weekdays.
        # A more robust solution would store these values as they happen.

        # For simplicity, let's assume we can get closes for the last 72 hours (3 days)
        # This covers Friday, Saturday, Sunday.
        long_closes = ctx.closes(72) # 3 days * 24 hours
        if len(long_closes) < 72:
            return None

        # Find Friday's last close (end of Friday)
        friday_close = None
        saturday_close = None
        sunday_close = None
        
        # Iterate backwards from Monday's current bar to find weekend boundaries
        # This is still a bit complex for a per-bar calculation.
        # Let's simplify: check the overall move from ~72 hours ago to current.
        # This isn't perfect "weekend", but a proxy for multi-day move.

        # Simplified weekend move: close 72 bars ago vs. current price before Monday open
        # This is a heuristic, as exact weekend boundaries are hard to pinpoint without iterating timestamps.
        # Instead, let's look at the price change from the beginning of Sunday to the end of Sunday.
        # This needs a more precise timestamp check.

        # Let's use a simpler heuristic: look at the last 48 bars (Sat/Sun) and see the change.
        # Find the close price at the start of Sunday (00:00 UTC Sunday)
        # Find the close price at the end of Sunday (23:00 UTC Sunday)
        sunday_start_price = None
        sunday_end_price = None
        
        # Iterate through past bars to find Sunday's start and end
        # This is still too complex for every bar.
        # The strategy requires a "weekend move". Let's approximate it as
        # the difference between the close 72 hours ago (roughly Friday) and the close 24 hours ago (roughly Sunday end).

        # Price at roughly Friday close (72 hours ago)
        price_72h_ago = ctx.closes(72)[0]

        # Price at roughly Sunday close (24 hours ago)
        price_24h_ago = ctx.closes(24)[0]

        weekend_move_pct = ((price_24h_ago - price_72h_ago) / price_72h_ago) * 100.0

        # Monday's confirmation: compare current price to Monday's open
        # We need the open price of the current Monday's 00:00 UTC bar
        # This requires looking back at timestamps.
        # Let's approximate: compare current price to price 13 hours ago (start of Monday US session)
        
        # Price at the start of the Monday US session (13:00 UTC)
        # This means the close of the bar that ended at 13:00 UTC.
        # So we need the close from `self.monday_confirmation_bars` bars ago.
        
        confirmation_closes = ctx.closes(self.monday_confirmation_bars + 1)
        if len(confirmation_closes) < self.monday_confirmation_bars + 1:
            return None

        monday_us_session_start_price = confirmation_closes[0]
        current_price = ctx.bar.close

        # Determine direction based on weekend move
        signal_direction = None
        confidence = 0.0

        if weekend_move_pct > self.weekend_move_threshold_pct: # Weekend up
            # Look for Monday confirmation of upward rebuild
            if current_price > monday_us_session_start_price:
                signal_direction = "long"
                confidence = min(0.6 + (weekend_move_pct / 10.0), 0.9) # Scale confidence with move
                
        elif weekend_move_pct < -self.weekend_move_threshold_pct: # Weekend down
            # Look for Monday confirmation of downward rebuild
            if current_price < monday_us_session_start_price:
                signal_direction = "short"
                confidence = min(0.6 + (abs(weekend_move_pct) / 10.0), 0.9) # Scale confidence with move

        if signal_direction and not ctx.has_position():
            self.last_trade_bar_index = ctx.bar_index
            self.last_weekend_processed = ctx.bar.timestamp.day # Mark this Monday as processed
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": f"monday_rebuild_{signal_direction}",
                    "weekend_move_pct": round(weekend_move_pct, 2),
                    "monday_us_start_price": round(monday_us_session_start_price, 2),
                    "current_price": round(current_price, 2),
                    "confirmation_bars": self.monday_confirmation_bars
                }
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic needed for this simple calendar strategy
        pass