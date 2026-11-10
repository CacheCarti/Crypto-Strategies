from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MondayRebuildETH(Strategy):
    METADATA = {
        "name": "MondayRebuildETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # 2 hours
        "warmup_bars": 72,              # For 3-day lookback + EMA
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.weekend_lookback_bars = 48  # Friday close to Sunday close
        self.monday_us_hours_start = 13  # UTC, roughly 9 AM ET
        self.monday_us_hours_end = 21    # UTC, roughly 5 PM ET
        self.cooldown_bars = 24          # Avoid immediate re-entry
        self.last_trade_bar = -self.cooldown_bars * 2 # Initialize to allow first trade

        self.ema_period = 20 # For faster trend confirmation on Monday
        self.min_weekend_move_pct = 0.5 # Minimum % move over weekend to be considered
        self.min_monday_confirm_pct = 0.2 # Minimum % move during US hours to confirm

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.weekend_lookback_bars + self.ema_period + 1)
        if len(closes) < self.weekend_lookback_bars + self.ema_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only execute on Monday during US trading hours
        is_monday = ctx.bar.timestamp.weekday() == 0 # Monday is 0
        if not is_monday:
            return None

        current_hour = ctx.bar.timestamp.hour
        if not (self.monday_us_hours_start <= current_hour < self.monday_us_hours_end):
            return None

        # Calculate weekend move (Friday close to Sunday close)
        # Assuming 1h bars, Friday close is weekend_lookback_bars ago from Monday open
        # This is an approximation. Let's define it as the close 48 bars ago (Sunday 00:00 UTC) to Monday 00:00 UTC.
        # More precisely, we need Friday's close. For simplicity, let's use the price 48 bars ago as 'weekend start'
        # and the price at Monday 00:00 UTC as 'weekend end'.
        # This strategy uses the current bar as Monday's US hour confirmation.
        # We need Sunday's last close (Monday 00:00 UTC) as the reference for Monday's move.
        
        # Find Sunday's close (the close at the end of Sunday, i.e., start of Monday 00:00 UTC)
        # This is ctx.closes(1)[0] if we're at Monday 00:00, or ctx.closes(current_hour+1)[0] for later Monday hours
        # Let's adjust: weekend_end_price is the close of the bar that ends at Sunday 23:00 UTC (i.e. Monday 00:00 UTC)
        # This would be closes[-(current_hour + 1)] if current_hour is 0
        # A simpler approach: get the close from `current_hour` bars ago, which was the close at Monday 00:00 UTC
        
        # To get the price at Monday 00:00 UTC (start of the day), we look back `current_hour` bars
        if len(closes) <= current_hour: # Not enough data for Monday's start
            return None
        monday_open_price = closes[-(current_hour + 1)] # The close of the bar ending at Monday 00:00 UTC

        # To get the price at the start of the weekend (Friday 00:00 UTC), we look back 72 hours (3 days) + current_hour
        # So, 72 hours before Monday 00:00 UTC.
        # This means 72 + current_hour bars ago from the current bar
        if len(closes) <= (72 + current_hour):
            return None
        weekend_start_price = closes[-(72 + current_hour + 1)] # Close at Friday 00:00 UTC

        weekend_move_pct = (monday_open_price - weekend_start_price) / weekend_start_price * 100.0

        # Now, Monday's US hours confirmation
        # Compare current price to Monday's open price
        monday_confirm_pct = (ctx.bar.close - monday_open_price) / monday_open_price * 100.0

        # EMA confirmation for direction
        ema_values = closes[-(self.ema_period + 1):] # Need enough for EMA up to current bar
        current_ema = self._ema(ema_values, self.ema_period)
        if current_ema is None:
            return None

        signal_direction = None
        confidence = 0.0

        # Long condition: Weekend down, Monday rebuilding up, confirmed by EMA
        if weekend_move_pct < -self.min_weekend_move_pct: # Weekend was negative
            if monday_confirm_pct > self.min_monday_confirm_pct: # Monday is moving up
                if ctx.bar.close > current_ema: # Price above EMA
                    signal_direction = "long"
                    confidence = min(1.0, 0.5 + abs(weekend_move_pct) * 0.1 + monday_confirm_pct * 0.1) # Scale confidence
                    confidence = max(0.5, confidence) # Min confidence
                    
        # Short condition: Weekend up, Monday rebuilding down, confirmed by EMA
        elif weekend_move_pct > self.min_weekend_move_pct: # Weekend was positive
            if monday_confirm_pct < -self.min_monday_confirm_pct: # Monday is moving down
                if ctx.bar.close < current_ema: # Price below EMA
                    signal_direction = "short"
                    confidence = min(1.0, 0.5 + abs(weekend_move_pct) * 0.1 + abs(monday_confirm_pct) * 0.1)
                    confidence = max(0.5, confidence)

        if signal_direction and not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": f"monday_rebuild_{signal_direction}",
                    "weekend_move_pct": round(weekend_move_pct, 2),
                    "monday_confirm_pct": round(monday_confirm_pct, 2),
                    "current_price": ctx.bar.close,
                    "ema": round(current_ema, 2),
                    "monday_open_price": round(monday_open_price, 2),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy yet
        pass