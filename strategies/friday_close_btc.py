from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeeklyCloseBTC(Strategy):
    METADATA = {
        "name": "Weekly Close BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 72000, # Roughly 20 hours (Friday evening to Sunday evening)
        "warmup_bars": 72, # For 72-period volume average
        "required_features": ["funding_rate_btcusdt"] # Add funding rate as a filter
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_cooldown_bars = 48 # Cooldown of 2 days (48 hours) after a trade
        self.last_trade_bar = -self.entry_cooldown_bars
        self.volume_period = 72 # 3 days of hourly bars for average volume
        self.confirmation_bars = 6 # The "final six UTC hours"
        self.min_volume_multiplier = 1.5 # Volume surge needed for confirmation
        self.min_price_change_pct = 0.5 # Minimum price change over confirmation bars
        self.friday_close_hour = 23 # UTC hour for Friday close (23:00 UTC)
        self.sunday_exit_hour = 23 # UTC hour for Sunday exit (23:00 UTC)

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.volume_period + 1)
        volumes = ctx.volumes(self.volume_period + 1)

        if len(closes) < self.volume_period + 1 or len(volumes) < self.volume_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.entry_cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Check for existing position
        if ctx.has_position():
            # Exit logic: Sunday evening UTC
            if current_weekday == 6 and current_hour == self.sunday_exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "sunday_evening_exit",
                    "exit_hour": current_hour,
                    "exit_weekday": current_weekday
                })
            return None # Hold position until Sunday exit

        # Entry logic: Friday close confirmation
        if current_weekday == 4 and current_hour == self.friday_close_hour: # Friday 23:00 UTC
            # Need at least `confirmation_bars` for analysis
            if len(closes) < self.confirmation_bars + 1 or len(volumes) < self.confirmation_bars + 1:
                return None

            # Average volume for the last `volume_period` bars (excluding current)
            avg_volume = self._sma(volumes[:-1], self.volume_period)
            if avg_volume is None:
                return None

            # Analyze the last `confirmation_bars` for decisive move
            confirmation_closes = closes[-(self.confirmation_bars + 1):]
            confirmation_volumes = volumes[-self.confirmation_bars:]

            # Calculate price change over confirmation period
            price_change_start = confirmation_closes[0]
            price_change_end = confirmation_closes[-1]
            price_change_pct = (price_change_end - price_change_start) / price_change_start * 100

            # Check for volume confirmation
            # Check if all volumes in the confirmation period are above average
            volume_confirmed = all(v > avg_volume * self.min_volume_multiplier for v in confirmation_volumes)

            signal_direction = None
            if price_change_pct > self.min_price_change_pct and volume_confirmed:
                signal_direction = "long"
            elif price_change_pct < -self.min_price_change_pct and volume_confirmed:
                signal_direction = "short"

            if signal_direction:
                # Add funding rate as a filter for higher confidence
                funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
                confidence = 0.6 # Base confidence

                if signal_direction == "long" and funding_rate < 0: # Negative funding rate is bullish for longs
                    confidence = min(0.9, confidence + abs(funding_rate) * 100) # Scale confidence by funding rate magnitude
                elif signal_direction == "short" and funding_rate > 0: # Positive funding rate is bullish for shorts
                    confidence = min(0.9, confidence + funding_rate * 100)

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": f"friday_close_confirmation_{signal_direction}_signal",
                        "price_change_pct": round(price_change_pct, 2),
                        "avg_volume": round(avg_volume, 2),
                        "current_volume_last_6hr_avg": round(sum(confirmation_volumes) / self.confirmation_bars, 2),
                        "volume_multiplier": self.min_volume_multiplier,
                        "funding_rate": round(funding_rate, 6),
                        "entry_hour": current_hour,
                        "entry_weekday": current_weekday
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, allowing a new trade after cooldown_bars
        self.last_trade_bar = ctx.bar_index