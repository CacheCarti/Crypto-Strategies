from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SundayWeeklyTransition(Strategy):
    METADATA = {
        "name": "Sunday Weekly Transition",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4, # Aim for 4-hour hold, exit before Monday 21 UTC
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt", "twitter_sentiment"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -self.METADATA["warmup_bars"] # Cooldown from start
        self.cooldown_bars = 12 # Wait 12 bars (12 hours) after a trade before entering again
        self.entry_threshold_pct = 0.5 # Percentage move to consider decisive
        self.volume_ratio_threshold = 1.5 # Volume must be 1.5x average for confirmation

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"] or len(volumes) < self.METADATA["warmup_bars"]:
            return None

        # Exit logic: close before Monday 21 UTC
        if ctx.has_position():
            if ctx.bar.timestamp.weekday() == 0 and ctx.bar.timestamp.hour >= 21:
                return ctx.signal("flat", confidence=0.9, metadata={
                    "reason": "monday_eod_exit",
                    "current_price": ctx.bar.close,
                    "exit_hour": ctx.bar.timestamp.hour
                })

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: Sunday 00:00 UTC (start of the week for this strategy)
        # Check if the current bar is the first bar after Sunday 00:00 UTC
        # This implies checking the bar before was not Sunday 00:00 UTC, and current is.
        # However, it's simpler to check for the bar *after* Sunday 00:00 UTC,
        # which means current bar is Sunday 01:00 UTC (or later, if we miss exact Sunday 00:00)
        # We want to identify the first significant move *after* the weekly open.
        # Let's look for the bar that *opens* on Sunday 00:00 UTC (which is the bar's timestamp)
        # and then check the move *from* that bar's open.

        # The strategy requires looking at the "first decisive move after Sunday UTC reopen".
        # This means we need to detect the Sunday 00:00 UTC bar and then evaluate the next few bars.
        # We will use a flag to remember the Sunday open price.
        if not hasattr(self, 'sunday_open_price'):
            self.sunday_open_price = None
            self.sunday_open_bar_index = -1

        # Reset Sunday open price at the start of each week
        if ctx.bar.timestamp.weekday() == 6 and ctx.bar.timestamp.hour == 0: # Sunday 00:00 UTC
            self.sunday_open_price = ctx.bar.open
            self.sunday_open_bar_index = ctx.bar_index
            return None # Don't trade on the exact Sunday open bar, wait for the move

        # If it's not Sunday 00:00 UTC, and we have a recorded Sunday open
        if self.sunday_open_price is not None and ctx.bar_index > self.sunday_open_bar_index:
            # Check for Monday continuation (only consider trades on Sunday or Monday)
            if ctx.bar.timestamp.weekday() not in [6, 0]: # Sunday=6, Monday=0
                self.sunday_open_price = None # Reset for next week
                return None

            # Look for a decisive move from Sunday open
            # We need to find the price at self.sunday_open_bar_index
            # This requires looking back at closes, which is tricky if we just set it.
            # Simpler: check if current bar's close shows a decisive move from the *recorded* sunday_open_price.
            # And only if it's within the first few bars after Sunday open (e.g., within 6 bars = 6 hours)
            if ctx.bar_index - self.sunday_open_bar_index > 6: # Only check first 6 hours after Sunday open
                self.sunday_open_price = None # Reset if we are past the window
                return None

            price_change_pct = (ctx.bar.close - self.sunday_open_price) / self.sunday_open_price * 100.0
            abs_price_change_pct = abs(price_change_pct)

            # Volume confirmation: current bar's volume vs average volume
            avg_volume_period = 20
            if len(volumes) < avg_volume_period:
                return None
            avg_volume = self._sma(volumes, avg_volume_period)
            if avg_volume == 0:
                return None
            volume_ratio = ctx.bar.volume / avg_volume

            # Sentiment filter: only trade if sentiment is neutral or positive for long, neutral or negative for short
            twitter_sentiment = ctx.features.get("twitter_sentiment", 0.0)
            
            # Funding rate as a confirmation (mildly positive funding for long, mildly negative for short)
            funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

            # Check for decisive move, volume confirmation, and not a single spike (implied by checking current bar's close vs sunday open)
            if abs_price_change_pct >= self.entry_threshold_pct and volume_ratio >= self.volume_ratio_threshold:
                if ctx.has_position():
                    return None # Already in a position

                signal_confidence = 0.6 # Base confidence

                if price_change_pct > 0: # Bullish move
                    if twitter_sentiment >= -0.1 and funding_rate >= -0.00005: # Neutral to positive sentiment & funding
                        signal_confidence += (twitter_sentiment * 0.2) + (min(funding_rate, 0.0001) * 1000) # Add some confidence based on sentiment/funding
                        self.last_trade_bar = ctx.bar_index
                        self.sunday_open_price = None # Reset for next week after trade
                        return ctx.signal("long", confidence=min(1.0, signal_confidence), metadata={
                            "reason": "sunday_open_bullish_transition",
                            "sunday_open": self.sunday_open_price,
                            "current_close": ctx.bar.close,
                            "price_change_pct": price_change_pct,
                            "volume_ratio": volume_ratio,
                            "twitter_sentiment": twitter_sentiment,
                            "funding_rate": funding_rate
                        })
                elif price_change_pct < 0: # Bearish move
                    if twitter_sentiment <= 0.1 and funding_rate <= 0.00005: # Neutral to negative sentiment & funding
                        signal_confidence += (abs(twitter_sentiment) * 0.2) + (max(funding_rate, -0.0001) * 1000) # Add some confidence
                        self.last_trade_bar = ctx.bar_index
                        self.sunday_open_price = None # Reset for next week after trade
                        return ctx.signal("short", confidence=min(1.0, signal_confidence), metadata={
                            "reason": "sunday_open_bearish_transition",
                            "sunday_open": self.sunday_open_price,
                            "current_close": ctx.bar.close,
                            "price_change_pct": price_change_pct,
                            "volume_ratio": volume_ratio,
                            "twitter_sentiment": twitter_sentiment,
                            "funding_rate": funding_rate
                        })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We could adapt parameters here based on win/loss, but for now, just log.
        # For this strategy, the cooldown is handled in on_bar.
        pass