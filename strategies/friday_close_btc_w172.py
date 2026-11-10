from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeeklyCloseConfirmation(Strategy):
    METADATA = {
        "name": "Weekly Close Confirmation",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 72 * 3600,  # Hold from Friday evening to Sunday evening
        "warmup_bars": 72,  # Need enough data for 6 hours + volume avg
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_cooldown = 0
        self.min_volume_multiplier = 1.5  # Volume must be X times higher than avg for confirmation
        self.price_move_threshold_pct = 0.005 # 0.5% move in 6 hours
        self.last_signal_bar = -1
        self.weekly_close_hour = 23 # UTC Friday 23:00 is the start of the final hour of the week
        self.exit_sunday_hour = 23 # UTC Sunday 23:00 to exit

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_signal_bar + 48: # Wait 48 bars (2 days) after any signal
            if ctx.has_position():
                return ctx.signal("hold")
            else:
                return None

        # Exit logic for existing positions
        if ctx.has_position():
            # Check if it's Sunday evening UTC
            if ctx.bar.timestamp.weekday() == 6 and ctx.bar.timestamp.hour == self.exit_sunday_hour:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "sunday_evening_exit",
                    "entry_price": ctx.position["entry_price"],
                    "current_price": ctx.bar.close,
                    "bars_held": ctx.bar_index - self.last_signal_bar
                })
            return ctx.signal("hold")

        # Entry logic - Only consider entries on Friday evening
        if ctx.bar.timestamp.weekday() == 4 and ctx.bar.timestamp.hour == self.weekly_close_hour:
            # We need the last 6 hours (6 bars) for price direction and volume confirmation
            if len(closes) < 6 or len(volumes) < 6:
                return None

            # Calculate average volume over a longer period for comparison
            long_term_volume_avg = self._sma(volumes, 24) # 24-hour average volume
            if long_term_volume_avg is None:
                return None

            # Analyze the last 6 bars (6 hours)
            recent_closes = closes[-6:]
            recent_volumes = volumes[-6:]

            # Price change over the last 6 hours
            price_change = recent_closes[-1] - recent_closes[0]
            price_change_pct = price_change / recent_closes[0]

            # Volume confirmation: average volume in last 6 hours vs long-term average
            avg_recent_volume = sum(recent_volumes) / len(recent_volumes)
            volume_confirmed = avg_recent_volume > (long_term_volume_avg * self.min_volume_multiplier)

            # Sentiment check: only trade if not in extreme fear/greed (neutral or moderate)
            fear_greed_index = ctx.features.get("fear_greed_index", 50)
            sentiment_neutral = 30 <= fear_greed_index <= 70

            # Funding rate check: avoid trading against strong funding bias
            funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
            funding_neutral = abs(funding_rate) < 0.0002 # e.g., < 0.02% per 8h

            # Determine signal based on price direction, volume, sentiment, and funding
            if price_change_pct > self.price_move_threshold_pct and volume_confirmed and sentiment_neutral and funding_neutral:
                # Strong bullish close on high volume
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "friday_weekly_close_bullish_confirmation",
                        "price_change_pct": price_change_pct,
                        "avg_recent_volume": avg_recent_volume,
                        "long_term_volume_avg": long_term_volume_avg,
                        "fear_greed_index": fear_greed_index,
                        "funding_rate": funding_rate,
                        "current_close": ctx.bar.close
                    }
                )
            elif price_change_pct < -self.price_move_threshold_pct and volume_confirmed and sentiment_neutral and funding_neutral:
                # Strong bearish close on high volume
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "friday_weekly_close_bearish_confirmation",
                        "price_change_pct": price_change_pct,
                        "avg_recent_volume": avg_recent_volume,
                        "long_term_volume_avg": long_term_volume_avg,
                        "fear_greed_index": fear_greed_index,
                        "funding_rate": funding_rate,
                        "current_close": ctx.bar.close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy, just logging
        pass