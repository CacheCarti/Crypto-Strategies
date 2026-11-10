from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FridayPositioningETH(Strategy):
    METADATA = {
        "name": "Friday Positioning ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Intended hold for a few hours, might extend over weekend
        "warmup_bars": 72, # Enough for 3-day lookback for volume and price action
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 24  # Wait 24 hours after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.entry_confirmation_bars = 6 # Last six UTC hours
        self.volume_lookback = 24 # For average volume calculation
        self.price_change_lookback = 6 # For decisive price change

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars or len(volumes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Only trade if no position is open
        if ctx.has_position():
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday=0, Sunday=6

        # Strategy: Look for decisive moves late Friday UTC
        # Focus on Friday 18:00 UTC to Friday 23:00 UTC (hours 18, 19, 20, 21, 22, 23)
        # Or early Saturday 00:00 UTC (hour 0) to catch last-minute positioning
        is_friday_night_or_early_saturday = (
            (current_weekday == 4 and current_hour >= 18) or # Friday 18:00 - 23:00 UTC
            (current_weekday == 5 and current_hour == 0)      # Saturday 00:00 UTC
        )

        if not is_friday_night_or_early_saturday:
            return None

        # Check for decisive move in the last `self.entry_confirmation_bars` hours
        # and confirmed by above-average volume
        recent_closes = closes[-self.entry_confirmation_bars:]
        recent_volumes = volumes[-self.entry_confirmation_bars:]

        if len(recent_closes) < self.entry_confirmation_bars or len(recent_volumes) < self.entry_confirmation_bars:
            return None

        # Calculate average volume over a longer period for comparison
        long_term_avg_volume = self._sma(volumes, self.volume_lookback)
        if long_term_avg_volume is None:
            return None

        # Check for significant volume in recent bars
        recent_avg_volume = sum(recent_volumes) / self.entry_confirmation_bars
        volume_confirmation = recent_avg_volume > long_term_avg_volume * 1.5 # 50% above average

        if not volume_confirmation:
            return None

        # Check for decisive price movement (e.g., significant change relative to recent volatility)
        price_change = recent_closes[-1] - recent_closes[0]
        recent_stddev = self._stddev(closes, self.price_change_lookback)

        if recent_stddev is None or recent_stddev == 0:
            return None

        # Define a threshold for "decisive" move, e.g., 1.5 standard deviations
        decisive_threshold = 1.5 * recent_stddev

        signal_direction: Optional[str] = None
        confidence = 0.0
        reason = ""

        if price_change > decisive_threshold:
            signal_direction = "long"
            confidence = min(0.8, abs(price_change / (2 * recent_stddev))) # Scale confidence based on strength
            reason = "friday_positioning_long_decisive_move_vol_confirm"
        elif price_change < -decisive_threshold:
            signal_direction = "short"
            confidence = min(0.8, abs(price_change / (2 * recent_stddev)))
            reason = "friday_positioning_short_decisive_move_vol_confirm"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "bar_timestamp": ctx.bar.timestamp.isoformat(),
                    "current_weekday": current_weekday,
                    "current_hour": current_hour,
                    "price_change_last_6h": price_change,
                    "recent_stddev": recent_stddev,
                    "recent_avg_volume": recent_avg_volume,
                    "long_term_avg_volume": long_term_avg_volume,
                    "close": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to ensure cooldown
        self.last_trade_bar = ctx.bar_index
        # Log closing details if needed
        # print(f"Position closed: {position['direction']} at {position['exit_price']} (Return: {position['return_bps']} bps)")