from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFridayPositioning(Strategy):
    METADATA = {
        "name": "ETH Friday Positioning",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold on average
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this calendar-based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_utc = 20 # Friday 20:00 UTC
        self.exit_hour_utc = 0   # Sunday 00:00 UTC
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

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
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check if we are in a position
        if ctx.has_position():
            # Check for Sunday 00:00 UTC exit
            if ctx.bar.timestamp.weekday() == 6 and ctx.bar.timestamp.hour == self.exit_hour_utc: # Sunday 00:00 UTC
                if ctx.position_direction() == "long":
                    reason = "friday_long_exit_sunday_utc_open"
                else:
                    reason = "friday_short_exit_sunday_utc_open"
                
                # Update last trade bar on exit
                self.last_trade_bar = ctx.bar_index
                
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": reason,
                    "bar_timestamp": ctx.bar.timestamp.isoformat(),
                    "exit_hour": self.exit_hour_utc,
                })
            return None # Hold position until exit condition

        # Entry logic: Friday 20:00 UTC
        if ctx.bar.timestamp.weekday() == 4 and ctx.bar.timestamp.hour == self.entry_hour_utc: # Friday 20:00 UTC
            closes = ctx.closes(self.METADATA["warmup_bars"])
            if len(closes) < self.METADATA["warmup_bars"]:
                return None

            # Calculate Z-score of recent price action to determine decisive move
            # We look at the last 6 hours (6 bars for 1h timeframe)
            recent_closes = ctx.closes(6)
            if len(recent_closes) < 6:
                return None

            # Z-score of the last close relative to the last 6 closes
            z_score_6h = self._zscore(recent_closes, 6)

            # SMA for trend confirmation
            sma_20 = self._sma(closes, 20)
            if sma_20 is None:
                return None

            current_close = ctx.bar.close

            # Long condition: Strong upward move (positive Z-score) and price above SMA
            if z_score_6h is not None and z_score_6h > 1.0 and current_close > sma_20:
                # Update last trade bar on entry
                self.last_trade_bar = ctx.bar_index
                
                return ctx.signal(
                    "long",
                    confidence=0.75 + min(0.25, z_score_6h / 3.0), # Scale confidence with z-score
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "friday_long_positioning_zscore_sma_confirm",
                        "bar_timestamp": ctx.bar.timestamp.isoformat(),
                        "entry_hour": self.entry_hour_utc,
                        "z_score_6h": z_score_6h,
                        "current_close": current_close,
                        "sma_20": sma_20,
                    }
                )

            # Short condition: Strong downward move (negative Z-score) and price below SMA
            elif z_score_6h is not None and z_score_6h < -1.0 and current_close < sma_20:
                # Update last trade bar on entry
                self.last_trade_bar = ctx.bar_index

                return ctx.signal(
                    "short",
                    confidence=0.75 + min(0.25, abs(z_score_6h) / 3.0), # Scale confidence with z-score
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "friday_short_positioning_zscore_sma_confirm",
                        "bar_timestamp": ctx.bar.timestamp.isoformat(),
                        "entry_hour": self.entry_hour_utc,
                        "z_score_6h": z_score_6h,
                        "current_close": current_close,
                        "sma_20": sma_20,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic needed for this strategy, but could be used to adjust cooldown_bars
        pass