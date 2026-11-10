from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendReversalMondayOpen(Strategy):
    METADATA = {
        "name": "Weekend Reversal Monday Open BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours, short for a swing but aligns with event
        "warmup_bars": 72, # Need enough data for weekend high/low
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.weekend_high = -1.0
        self.weekend_low = 999999999.0
        self.weekend_start_price = 0.0
        self.last_trade_bar_index = -self.METADATA["warmup_bars"]

        # Cooldown period in bars
        self.cooldown_bars = 24 # Wait 24 hours after a trade

        # Thresholds
        self.min_weekend_move_pct = 0.005 # 0.5% move over weekend to be significant
        self.confirmation_bars = 4 # How many bars into Monday's US session to confirm
        self.confirmation_threshold_pct = 0.002 # 0.2% move in confirmation period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        current_time = ctx.bar.timestamp

        # --- Cooldown Logic ---
        if ctx.bar_index < self.last_trade_bar_index + self.cooldown_bars:
            return None

        # --- Weekend Data Collection (Friday Close to Sunday Close) ---
        # Friday 16:00 UTC to Sunday 23:00 UTC
        # Friday (weekday 4) 16:00 UTC is close of US market, start of weekend for some.
        # Sunday (weekday 6) 23:00 UTC is just before Monday starts.
        if current_time.weekday() == 4 and current_time.hour == 16: # Friday 16:00 UTC
            self.weekend_start_price = current_price
            self.weekend_high = current_price
            self.weekend_low = current_price

        if current_time.weekday() in [5, 6] or \
           (current_time.weekday() == 4 and current_time.hour >= 16) or \
           (current_time.weekday() == 0 and current_time.hour < 0): # Up to Monday 00:00 UTC
            self.weekend_high = max(self.weekend_high, ctx.bar.high)
            self.weekend_low = min(self.weekend_low, ctx.bar.low)

        # --- Monday US Open Confirmation and Signal Generation ---
        # Monday (weekday 0) 14:00 UTC (10 AM EST) is typically US market open
        # We look for a confirmation move from 14:00 UTC for `confirmation_bars`
        if current_time.weekday() == 0 and current_time.hour == 14 + self.confirmation_bars:
            if self.weekend_start_price == 0: # No valid weekend start recorded
                return None

            # Calculate weekend move
            weekend_move_up = (self.weekend_high - self.weekend_start_price) / self.weekend_start_price
            weekend_move_down = (self.weekend_low - self.weekend_start_price) / self.weekend_start_price

            # Check for significant weekend move
            is_significant_move = False
            if weekend_move_up > self.min_weekend_move_pct:
                is_significant_move = True
            elif abs(weekend_move_down) > self.min_weekend_move_pct:
                is_significant_move = True

            if not is_significant_move:
                # Reset weekend data for next week
                self.weekend_start_price = 0.0
                self.weekend_high = -1.0
                self.weekend_low = 999999999.0
                return None

            # Check confirmation during Monday US open hours
            confirmation_closes = ctx.closes(self.confirmation_bars + 1)
            if len(confirmation_closes) <= self.confirmation_bars:
                return None # Not enough bars for confirmation

            confirmation_start_price = confirmation_closes[0] # Price at Monday 14:00 UTC
            confirmation_end_price = current_price # Price at Monday 14:00 + confirmation_bars UTC

            monday_confirmation_move = (confirmation_end_price - confirmation_start_price) / confirmation_start_price

            signal_direction = None
            reason = ""
            confidence = 0.0

            # Weekend sell-off, Monday open confirms downward pressure -> SHORT
            if weekend_move_down < -self.min_weekend_move_pct and monday_confirmation_move < -self.confirmation_threshold_pct:
                signal_direction = "short"
                confidence = min(0.8, abs(weekend_move_down) * 100 / self.min_weekend_move_pct * 0.4)
                reason = "weekend_selloff_monday_confirmation"
            # Weekend rally, Monday open confirms upward pressure -> LONG
            elif weekend_move_up > self.min_weekend_move_pct and monday_confirmation_move > self.confirmation_threshold_pct:
                signal_direction = "long"
                confidence = min(0.8, weekend_move_up * 100 / self.min_weekend_move_pct * 0.4)
                reason = "weekend_rally_monday_confirmation"

            if signal_direction and not ctx.has_position():
                self.last_trade_bar_index = ctx.bar_index
                # Reset weekend data after signal, to avoid re-triggering on same weekend
                self.weekend_start_price = 0.0
                self.weekend_high = -1.0
                self.weekend_low = 999999999.0
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "weekend_start_price": self.weekend_start_price,
                        "weekend_high": self.weekend_high,
                        "weekend_low": self.weekend_low,
                        "weekend_move_pct_up": weekend_move_up,
                        "weekend_move_pct_down": weekend_move_down,
                        "monday_confirmation_move_pct": monday_confirmation_move,
                        "current_price": current_price
                    }
                )

        # Clear weekend data if it's Tuesday or later, to ensure fresh calculation next week
        if current_time.weekday() == 1 and current_time.hour == 0:
            self.weekend_start_price = 0.0
            self.weekend_high = -1.0
            self.weekend_low = 999999999.0

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic needed for this strategy, but could be added here.
        # For example, if many consecutive losses, increase cooldown_bars or min_weekend_move_pct.
        pass