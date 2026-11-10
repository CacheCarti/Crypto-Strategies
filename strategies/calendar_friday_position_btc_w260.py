from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FridayPositioningMove(Strategy):
    METADATA = {
        "name": "FridayPositioningMove",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold
        "warmup_bars": 50, # Need enough bars for EMA and recent price action
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.entry_cooldown_bars = 12 # Cooldown for 12 hours after an entry
        self.last_entry_bar = -self.entry_cooldown_bars # Initialize to allow immediate entry
        self.last_signal_direction = None # Track last signal to prevent redundant signals

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period * 2) # Need more for EMA calculation stability
        highs = ctx.highs(self.ema_period * 2)
        lows = ctx.lows(self.ema_period * 2)
        volumes = ctx.volumes(self.ema_period * 2)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_entry_bar + self.entry_cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Friday positioning move: Look for entry opportunities on Friday (weekday 4)
        # Specifically, target late Friday afternoon/evening UTC for positioning
        is_friday_late = current_weekday == 4 and current_hour >= 16 and current_hour <= 23 # UTC 16:00 - 23:00

        # Sunday reversal/unwinding: Look for flat signals on Sunday late hours
        is_sunday_late = current_weekday == 6 and current_hour >= 20 # UTC 20:00 onwards

        current_price = ctx.bar.close
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # Calculate average volume for the last 12 hours
        if len(volumes) < 12:
            return None
        avg_volume_12h = sum(volumes[-12:]) / 12.0

        # --- Entry Logic (Friday) ---
        if is_friday_late and not ctx.has_position():
            # Check for decisive movement in the last 6 hours (strong trend)
            # Use a simple measure: compare current price to 6-hour ago price
            # And confirm with volume
            if len(closes) < 6:
                return None

            price_6h_ago = closes[-7] # -7 because closes[-1] is current, closes[-6] is 5 hours ago
            price_change_6h = (current_price - price_6h_ago) / price_6h_ago

            # Check for above average volume during this period
            volume_6h_avg = sum(volumes[-6:]) / 6.0
            volume_confirmation = volume_6h_avg > avg_volume_12h * 1.2 # 20% higher than 12h average

            # Funding rate as a sentiment/regime filter: positive for long, negative for short
            funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

            signal_direction = None
            if price_change_6h > 0.005 and current_price > ema and volume_confirmation and funding_rate > 0.0001: # 0.5% up in 6h
                signal_direction = "long"
            elif price_change_6h < -0.005 and current_price < ema and volume_confirmation and funding_rate < -0.0001: # 0.5% down in 6h
                signal_direction = "short"

            if signal_direction:
                self.last_entry_bar = ctx.bar_index
                self.last_signal_direction = signal_direction
                return ctx.signal(
                    signal_direction,
                    confidence=0.7,
                    metadata={
                        "reason": f"friday_positioning_{signal_direction}_move",
                        "price_change_6h": f"{price_change_6h:.4f}",
                        "current_price": current_price,
                        "ema": ema,
                        "volume_confirmation": volume_confirmation,
                        "funding_rate": funding_rate,
                        "regime": ctx.regime,
                    }
                )

        # --- Exit Logic (Sunday or if position held too long) ---
        if ctx.has_position():
            # Exit on Sunday late hours, or if holding too long (e.g., beyond Sunday)
            if is_sunday_late:
                self.last_entry_bar = ctx.bar_index # Reset cooldown after exiting
                self.last_signal_direction = None
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "sunday_unwind_exit",
                        "current_price": current_price,
                        "entry_price": ctx.position["entry_price"],
                        "bars_held": ctx.bar_index - self.last_entry_bar,
                        "regime": ctx.regime,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already handle `self.last_entry_bar` reset in on_bar for planned exits.
        # This is for stop-loss/take-profit exits, ensure cooldown is respected.
        # If a position closes for any reason, we should cool down.
        self.last_entry_bar = ctx.bar_index
        self.last_signal_direction = None