from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFridayPositioning(Strategy):
    METADATA = {
        "name": "EthFridayPositioning",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # Aim for 2-3 hour hold, could be longer for event
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this specific logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 48  # Cooldown for 2 days (48 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.ema_period = 20 # For trend confirmation
        self.volume_ma_period = 20 # For volume confirmation
        self.min_volume_ratio = 1.2 # Volume must be 20% higher than average
        self.min_price_move_pct = 0.5 # Minimum 0.5% move in 6 hours for decisiveness
        self.max_entry_window_hours = 6 # Max 6 hours to enter after Friday UTC close
        self.max_exit_window_hours = 24 # Exit before Sunday UTC close if still open

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars or len(volumes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check for existing position to manage exits
        if ctx.has_position():
            # If position is still open by Sunday UTC close, exit
            if current_weekday == 6 and current_hour >= 0: # Sunday 00:00 UTC
                if ctx.position_direction() == "long":
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "sunday_utc_close_exit", "entry_price": ctx.position["entry_price"], "current_price": ctx.bar.close})
                elif ctx.position_direction() == "short":
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "sunday_utc_close_exit", "entry_price": ctx.position["entry_price"], "current_price": ctx.bar.close})
            return None # Hold existing position

        # Entry logic: Late Friday positioning
        # We are looking for the bar *after* Friday 23:00 UTC, which is Saturday 00:00 UTC
        # Or, more generally, the first few hours after Friday UTC close.
        # This gives a window for "late Friday positioning" to play out.
        is_friday_night_window = (current_weekday == 4 and current_hour >= 23) or \
                                 (current_weekday == 5 and current_hour < self.max_entry_window_hours)

        if not is_friday_night_window:
            return None

        # Check for decisive move in the last 6 hours leading up to the current bar
        lookback_period_for_move = 6
        if len(closes) < lookback_period_for_move:
            return None

        price_change_6h = (closes[-1] - closes[-lookback_period_for_move]) / closes[-lookback_period_for_move]
        abs_price_change_6h_pct = abs(price_change_6h) * 100

        if abs_price_change_6h_pct < self.min_price_move_pct:
            return None # Not decisive enough

        # Volume confirmation: current volume above average
        avg_volume = self._sma(volumes, self.volume_ma_period)
        if avg_volume is None or ctx.bar.volume < avg_volume * self.min_volume_ratio:
            return None # Not confirmed by volume

        # Trend confirmation using EMA
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        signal_direction = None
        reason = ""
        confidence = 0.0

        # Long entry: Decisive upward move, confirmed by volume, and price above EMA
        if price_change_6h > 0 and ctx.bar.close > ema:
            signal_direction = "long"
            reason = "friday_positioning_long_decisive_up_volume_ema_confirm"
            confidence = 0.75

        # Short entry: Decisive downward move, confirmed by volume, and price below EMA
        elif price_change_6h < 0 and ctx.bar.close < ema:
            signal_direction = "short"
            reason = "friday_positioning_short_decisive_down_volume_ema_confirm"
            confidence = 0.75

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "price_change_6h_pct": abs_price_change_6h_pct,
                    "current_volume_ratio": ctx.bar.volume / avg_volume,
                    "current_close": ctx.bar.close,
                    "ema_period": self.ema_period,
                    "ema_value": ema,
                    "bar_timestamp": str(ctx.bar.timestamp)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to apply cooldown even if position was closed by SL/TP
        self.last_trade_bar = ctx.bar_index
        # Log for analysis, could be used for adaptive sizing in a more complex strategy
        # print(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps, bars held: {position['bars_held']}")
        pass