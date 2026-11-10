from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendReversalMondayConfirm(Strategy):
    METADATA = {
        "name": "Weekend Reversal Monday Confirm",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours expected hold for confirmation
        "warmup_bars": 72, # Need enough bars for 72 hours (3 days) of weekend data + EMA
        "required_features": [], # No external features needed for this core logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.weekend_lookback_bars = 48 # 2 days * 24 hours/day = 48 bars
        self.monday_confirmation_period = 4 # How many hours into Monday to look for confirmation
        self.ema_period = 12 # EMA for short-term trend confirmation
        self.cooldown_bars = 24 # Cooldown for 24 hours after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow trade immediately

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Check if it's Monday UTC
        # Monday is weekday() == 0
        if ctx.bar.timestamp.weekday() != 0:
            return None

        # Check if it's within the first `monday_confirmation_period` hours of Monday
        # US trading hours typically start around 13:00 UTC, so we look for confirmation after that.
        # Let's say 13:00-17:00 UTC for confirmation
        if not (13 <= ctx.bar.timestamp.hour < (13 + self.monday_confirmation_period)):
            return None

        # --- Weekend Price Analysis ---
        # Need at least 48 bars for Friday close to Sunday close
        if len(closes) < self.weekend_lookback_bars + 1:
            return None

        # Find Friday's close (approx. 48 bars ago if current bar is Monday morning)
        # We need to find the actual Friday close, not just 48 bars back.
        # This requires iterating back in time or using a more robust datetime check.
        # For simplicity, we'll approximate: assume the bar 48 bars ago was around Friday close.
        # A more precise implementation would find the last bar of Friday.
        # Given the 1-hour bar, 48 bars is roughly 2 full days.
        # If current bar is Monday 13:00 UTC (bar_index T)
        # Sunday 13:00 UTC (T-24)
        # Saturday 13:00 UTC (T-48)
        # Friday 13:00 UTC (T-72)
        # Let's look at the close before the weekend started (Friday close)
        # And the open of the current Monday.

        # To find Friday's close, we need to go back 24 hours (Sunday) + 24 hours (Saturday)
        # + the number of hours into Monday we are.
        # If it's Monday 13:00 UTC, then Friday's close would be 48 + 13 = 61 bars ago from the start of Monday.
        # A simpler approximation: compare current price to the price 48 hours ago.
        # This assumes a continuous block of 48 bars.

        # Let's define the weekend as the 48 bars leading up to the current Monday morning.
        # Calculate the price change over the weekend
        weekend_start_price = closes[-(self.weekend_lookback_bars + 1)] # Price 48 hours before the last bar of the weekend
        weekend_end_price = closes[- (self.monday_confirmation_period + 1)] # Price just before Monday confirmation window
        
        # This is a bit tricky with fixed bar counts. Let's simplify:
        # What was the price change from Friday close (approx 72 bars ago) to Sunday close (approx 24 bars ago)?
        # And then from Sunday close to current Monday price.

        # More robust weekend price calculation:
        # Find the last close before the weekend (Friday close)
        # Find the first open after the weekend (Monday open)
        # This requires more complex datetime iteration which might exceed complexity limits.

        # Let's use a simpler approach:
        # Compare the current price to the price 72 bars ago (approx Friday 13:00 UTC if now Monday 13:00 UTC)
        # And compare the current price to the price 24 bars ago (approx Sunday 13:00 UTC)

        # Price at the beginning of the previous weekend (approx. Friday 13:00 UTC)
        price_friday_approx = closes[-72] if len(closes) >= 72 else None
        # Price at the end of the previous weekend (approx. Sunday 13:00 UTC)
        price_sunday_approx = closes[-24] if len(closes) >= 24 else None

        if price_friday_approx is None or price_sunday_approx is None:
            return None

        # Weekend move: Friday close to Sunday close
        weekend_move_pct = (price_sunday_approx - price_friday_approx) / price_friday_approx * 100

        # Monday confirmation: Current price vs Sunday close
        monday_move_pct = (ctx.bar.close - price_sunday_approx) / price_sunday_approx * 100

        # --- Directional Logic ---
        signal_direction = None
        reason = ""
        confidence = 0.0

        # Long scenario: Price dropped over weekend (bearish weekend), but Monday is showing strength (reversal)
        if weekend_move_pct < -0.5: # Significant drop over weekend
            if monday_move_pct > 0.3: # Monday showing positive move, confirming reversal
                # Further confirmation with EMA
                current_ema = self._ema(closes, self.ema_period)
                if current_ema is not None and ctx.bar.close > current_ema:
                    signal_direction = "long"
                    reason = "weekend_bearish_monday_bull_reversal"
                    confidence = min(1.0, abs(weekend_move_pct * 0.1) + (monday_move_pct * 0.1)) # Scale confidence
                    confidence = max(0.4, confidence) # Minimum confidence
                    
        # Short scenario: Price rose over weekend (bullish weekend), but Monday is showing weakness (reversal)
        elif weekend_move_pct > 0.5: # Significant rise over weekend
            if monday_move_pct < -0.3: # Monday showing negative move, confirming reversal
                # Further confirmation with EMA
                current_ema = self._ema(closes, self.ema_period)
                if current_ema is not None and ctx.bar.close < current_ema:
                    signal_direction = "short"
                    reason = "weekend_bullish_monday_bear_reversal"
                    confidence = min(1.0, abs(weekend_move_pct * 0.1) + abs(monday_move_pct * 0.1)) # Scale confidence
                    confidence = max(0.4, confidence) # Minimum confidence

        if signal_direction and not ctx.has_position():
            self.last_trade_bar = ctx.bar_index # Set cooldown
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "weekend_move_pct": weekend_move_pct,
                    "monday_move_pct": monday_move_pct,
                    "current_price": ctx.bar.close,
                    "ema": current_ema,
                    "cooldown_set_until": self.last_trade_bar + self.cooldown_bars,
                }
            )
        
        # If we have a position and conditions reverse, or if it's getting late on Monday, consider flattening.
        if ctx.has_position() and ctx.position_direction() == "long" and signal_direction == "short":
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("flat", confidence=0.7, metadata={"reason": "flip_to_short_signal", "weekend_move_pct": weekend_move_pct, "monday_move_pct": monday_move_pct})
        
        if ctx.has_position() and ctx.position_direction() == "short" and signal_direction == "long":
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("flat", confidence=0.7, metadata={"reason": "flip_to_long_signal", "weekend_move_pct": weekend_move_pct, "monday_move_pct": monday_move_pct})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Adaptive logic here. For now, just log.
        pass