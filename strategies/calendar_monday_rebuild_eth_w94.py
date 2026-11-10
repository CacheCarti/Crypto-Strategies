from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendReversalMondayOpen(Strategy):
    METADATA = {
        "name": "Weekend Reversal Monday Open",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-hour target hold
        "warmup_bars": 72, # For 3-day lookback for weekend move (72 hours)
    }

    def initialize(self, ctx: BarContext) -> None:
        self.weekend_lookback_bars = 48 # Friday 00:00 UTC to Sunday 23:00 UTC (48 bars)
        self.monday_confirmation_bars = 4 # First 4 hours of Monday UTC (00:00 - 03:00 UTC)
        self.cooldown_bars = 24 # 24-hour cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.weekend_lookback_bars + self.monday_confirmation_bars + 1)
        if len(closes) < self.weekend_lookback_bars + self.monday_confirmation_bars + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only consider trades on Monday
        if ctx.bar.timestamp.weekday() != 0: # Monday is 0
            return None

        # Only consider trades during the first few US hours (e.g., 12 UTC to 16 UTC)
        # This is a proxy for US open, confirming the early Monday move.
        # Original request was "first US hours confirm" - let's define as 12-16 UTC for ETH market
        if not (12 <= ctx.bar.timestamp.hour < 16):
            return None

        # Check if we already have a position
        if ctx.has_position():
            return None

        # Calculate weekend move (Friday open to Sunday close)
        # Friday 00:00 UTC is (weekend_lookback_bars + monday_confirmation_bars) bars ago
        # Sunday 23:00 UTC is (monday_confirmation_bars + 1) bars ago
        # We need Friday's open, which is 72 hours (3 days) before Monday 00:00 UTC.
        # Let's simplify: compare close of Friday 00:00 UTC to close of Sunday 23:00 UTC
        # This means closes[- (self.monday_confirmation_bars + self.weekend_lookback_bars + 1)] is Friday 00:00 UTC close
        # closes[- (self.monday_confirmation_bars + 1)] is Sunday 23:00 UTC close
        
        # Ensure we have enough bars specifically for the weekend window before Monday's confirmation
        # To get Friday 00:00 UTC (start of weekend for calculation)
        # We need to look back 48 bars for the weekend + 1 bar for Monday 00:00 UTC + 4 bars for confirmation window
        # Total bars needed: 48 (weekend) + 4 (Mon confirm) + 1 (Mon 00:00) = 53
        
        # Let's define weekend_start_index and weekend_end_index relative to current bar
        # Current bar is closes[-1]
        # Monday 00:00 UTC (start of Monday) is closes[-(self.monday_confirmation_bars + 1)]
        # Sunday 23:00 UTC (end of weekend) is closes[-(self.monday_confirmation_bars + 1)]
        # Friday 00:00 UTC (start of weekend for calculation) is closes[-(self.monday_confirmation_bars + self.weekend_lookback_bars + 1)]

        # Ensure we are at the specific Monday confirmation hour (e.g., 12 UTC)
        # and that the weekend data is available.
        # The earliest Monday bar we trade on is 00:00 UTC + 12 hours = 12:00 UTC
        # So we need closes up to current bar (12 UTC Mon), plus 48 bars for weekend.
        # Total bars for closes: 12 + 48 = 60 bars.
        
        # To calculate weekend move: Friday 00:00 UTC close vs Sunday 23:00 UTC close
        # Find the index for Friday 00:00 UTC. It's 72 bars before Monday 00:00 UTC.
        # If current bar is Monday 12:00 UTC, then Monday 00:00 UTC is 12 bars ago.
        # So, Friday 00:00 UTC is 12 + 72 = 84 bars ago.
        
        # This requires more precise time-based indexing. Let's simplify.
        # We need the close price 72 hours (3 days) ago (Friday 00:00 UTC)
        # And the close price 24 hours (1 day) ago (Sunday 12:00 UTC, if current is Mon 12:00 UTC)
        # This is too complex for simple array indexing without knowing exact bar timestamps.

        # Alternative: Calculate the change from Sunday's close to Monday's current price.
        # This captures the "rebuilding direction" more directly.
        # Sunday's close (23:00 UTC) is 13 bars ago if current bar is Monday 12:00 UTC.
        # (12 hours of Monday + 1 hour of Sunday = 13 bars)
        
        # Let's define the weekend move as the difference between Friday's close and Sunday's close.
        # Friday's close (23:00 UTC) is 24 + 24 + ctx.bar.timestamp.hour + 1 bars ago.
        # Sunday's close (23:00 UTC) is ctx.bar.timestamp.hour + 1 bars ago.
        # This is error prone with varying current hour.

        # Simpler approach: Calculate the move from Sunday 00:00 UTC open to Monday 00:00 UTC open.
        # The "weekend move" is the net change from the open of the first bar of Saturday to the close of the last bar of Sunday.
        # This is 48 bars.
        # Let's identify the start of Monday (00:00 UTC).
        # This is the bar where ctx.bar.timestamp.weekday() == 0 and ctx.bar.timestamp.hour == 0.
        
        # We need the price at the close of Sunday (23:00 UTC).
        # And the price at the open of Monday (00:00 UTC).
        # And the current price (Monday, during confirmation hours).

        # If current bar is Monday 12:00 UTC:
        # Sunday 23:00 UTC close is closes[-13]
        # Monday 00:00 UTC open is opens[-12]
        
        # Let's define "weekend move" as the change from Friday 23:00 UTC close to Sunday 23:00 UTC close.
        # Friday 23:00 UTC is 24*2 = 48 hours before Sunday 23:00 UTC.
        # If current bar is Monday 12:00 UTC, Sunday 23:00 UTC is 13 bars ago.
        # So Friday 23:00 UTC is 13 + 48 = 61 bars ago.
        
        friday_close_idx = -(ctx.bar.timestamp.hour + 1 + 24 + 24) # Friday 23:00 UTC
        sunday_close_idx = -(ctx.bar.timestamp.hour + 1) # Sunday 23:00 UTC
        
        if len(closes) < abs(friday_close_idx) or len(closes) < abs(sunday_close_idx):
            return None

        friday_close = closes[friday_close_idx]
        sunday_close = closes[sunday_close_idx]
        
        weekend_change_pct = ((sunday_close - friday_close) / friday_close) * 100

        # Monday's rebuilding direction: compare Monday's open to current price
        # Monday 00:00 UTC open is ctx.opens[-(ctx.bar.timestamp.hour)]
        monday_open = ctx.opens(-(ctx.bar.timestamp.hour))
        current_price = ctx.bar.close

        monday_rebuilding_change_pct = ((current_price - monday_open) / monday_open) * 100

        # Trading logic:
        # If weekend was strongly down, look for Monday up-reversal confirmed by early US hours.
        # If weekend was strongly up, look for Monday down-reversal confirmed by early US hours.
        
        # Thresholds for "strong" move and confirmation
        weekend_move_threshold = 0.8 # 0.8% move over the weekend
        monday_confirmation_threshold = 0.3 # 0.3% move in Monday's early US hours

        signal_direction = None
        reason = ""
        confidence = 0.5

        if weekend_change_pct < -weekend_move_threshold and monday_rebuilding_change_pct > monday_confirmation_threshold:
            # Weekend down, Monday recovering
            signal_direction = "long"
            reason = "weekend_down_monday_recovery"
            confidence = min(0.5 + abs(weekend_change_pct)/5.0 + monday_rebuilding_change_pct/5.0, 0.9)
        elif weekend_change_pct > weekend_move_threshold and monday_rebuilding_change_pct < -monday_confirmation_threshold:
            # Weekend up, Monday retracing
            signal_direction = "short"
            reason = "weekend_up_monday_retracement"
            confidence = min(0.5 + abs(weekend_change_pct)/5.0 + abs(monday_rebuilding_change_pct)/5.0, 0.9)

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "weekend_change_pct": weekend_change_pct,
                    "monday_rebuilding_change_pct": monday_rebuilding_change_pct,
                    "friday_close": friday_close,
                    "sunday_close": sunday_close,
                    "monday_open": monday_open,
                    "current_price": current_price,
                    "bar_hour": ctx.bar.timestamp.hour
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy
        pass