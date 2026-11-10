from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeeklyTransitionStrategy(Strategy):
    METADATA = {
        "name": "Weekly Transition Strategy",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # Aim for 2-3 hours, but close before Monday evening
        "warmup_bars": 50,  # Need enough data for EMAs and volume calculations
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_short_period = 10
        self.ema_long_period = 30
        self.volume_period = 20
        self.cooldown_bars = 5
        self.last_trade_bar_index = -self.cooldown_bars

        # State to track for weekly transition logic
        self.last_sunday_close = None
        self.monday_signal_fired = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial value
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        volumes = ctx.volumes(self.volume_period + 1)

        if len(closes) < self.warmup_bars or len(volumes) < self.volume_period:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Reset for a new week on Sunday 00:00 UTC (first bar of Sunday)
        if current_weekday == 6 and current_hour == 0 and self.last_sunday_close is None:
            self.last_sunday_close = ctx.bar.open # Use open of first Sunday bar as reference
            self.monday_signal_fired = False
            return None # Don't trade on Sunday

        # Update last_sunday_close if it was set in a previous week
        if current_weekday == 6 and current_hour == 0 and self.last_sunday_close is not None:
            # New week, reset for next Monday's logic
            self.last_sunday_close = ctx.bar.open
            self.monday_signal_fired = False

        # If it's Sunday, just track the close and don't trade
        if current_weekday == 6:
            self.last_sunday_close = ctx.bar.close # Keep track of the last Sunday close
            return None

        # --- Weekly Transition Logic (Monday Morning) ---
        if current_weekday == 0 and current_hour >= 0 and current_hour < 12 and not self.monday_signal_fired: # Monday 00:00 - 11:00 UTC
            if self.last_sunday_close is None:
                return None # No Sunday reference point yet

            # Check for a decisive move and volume confirmation
            price_change_from_sunday = (ctx.bar.close - self.last_sunday_close) / self.last_sunday_close
            avg_volume = self._sma(volumes, self.volume_period)

            if avg_volume is None or avg_volume == 0:
                return None

            current_volume_zscore = self._zscore(volumes, self.volume_period)

            # Define thresholds for a "decisive move" and "volume support"
            price_move_threshold = 0.005 # 0.5% move
            volume_zscore_threshold = 0.5 # Current volume is 0.5 std dev above average

            # Cooldown check
            if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
                return None

            if price_change_from_sunday > price_move_threshold and current_volume_zscore > volume_zscore_threshold:
                # Long signal
                self.monday_signal_fired = True
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("long", confidence=0.7,
                                  stop_loss_bps=self.METADATA["declared_sl_bps"],
                                  take_profit_bps=self.METADATA["declared_tp_bps"],
                                  horizon_seconds=self.METADATA["declared_hold_seconds"],
                                  metadata={
                                      "reason": "monday_continuation_long",
                                      "price_change_from_sunday_pct": price_change_from_sunday * 100,
                                      "volume_zscore": current_volume_zscore,
                                      "entry_bar_hour": current_hour,
                                      "last_sunday_close": self.last_sunday_close
                                  })
            elif price_change_from_sunday < -price_move_threshold and current_volume_zscore > volume_zscore_threshold:
                # Short signal
                self.monday_signal_fired = True
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("short", confidence=0.7,
                                  stop_loss_bps=self.METADATA["declared_sl_bps"],
                                  take_profit_bps=self.METADATA["declared_tp_bps"],
                                  horizon_seconds=self.METADATA["declared_hold_seconds"],
                                  metadata={
                                      "reason": "monday_continuation_short",
                                      "price_change_from_sunday_pct": price_change_from_sunday * 100,
                                      "volume_zscore": current_volume_zscore,
                                      "entry_bar_hour": current_hour,
                                      "last_sunday_close": self.last_sunday_close
                                  })

        # --- Exit Logic for Monday Trades ---
        # Close any open position before Monday 21:00 UTC
        if ctx.has_position() and current_weekday == 0 and current_hour >= 21:
            position_dir = ctx.position_direction()
            self.last_trade_bar_index = ctx.bar_index # Cooldown after forced exit
            return ctx.signal("flat", confidence=0.9, metadata={"reason": f"monday_end_of_day_exit_{position_dir}"})

        # If no Monday signal fired and it's past Monday morning, reset for next week
        if current_weekday > 0 and self.monday_signal_fired:
            self.monday_signal_fired = False
            self.last_sunday_close = None # Clear for next Sunday's capture

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We can use this for logging or adaptive sizing, but for this strategy,
        # we primarily use it to ensure cooldown.
        # The self.last_trade_bar_index is updated upon signal, including 'flat' signals.
        pass