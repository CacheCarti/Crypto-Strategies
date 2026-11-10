from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MonthEndMomentum(Strategy):
    METADATA = {
        "name": "Month End Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours, but could be longer for month-end
        "warmup_bars": 72, # Need enough bars for 3-day momentum
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 48 # Cooldown for 2 days after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.last_month = None
        self.month_end_processed = False

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_month = ctx.bar.timestamp.month
        current_day = ctx.bar.timestamp.day
        current_hour = ctx.bar.timestamp.hour

        # Update last_month for month transition detection
        if self.last_month is None:
            self.last_month = current_month
        elif current_month != self.last_month:
            self.month_end_processed = False # Reset for new month's event
            self.last_month = current_month

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Determine if it's the last day of the month or first day of the next month
        is_month_end = False
        is_month_start = False

        # Simple check for last day of month
        # This is not perfect for all months (e.g., Feb), but good enough for a general strategy
        if current_day in [28, 29, 30, 31] and ctx.bar.timestamp.day != (ctx.bar.timestamp.replace(day=28) + ctx.bar.timestamp.resolution).day:
             # Check if current day is the last day of the month (e.g., if tomorrow is day 1 of next month)
            try:
                next_day = ctx.bar.timestamp.replace(day=current_day + 1)
            except ValueError: # day overflow, means it's end of month
                is_month_end = True
            else:
                if next_day.month != current_month:
                    is_month_end = True

        if current_day == 1:
            is_month_start = True

        # Only execute logic around month-end/month-start and only once per transition
        if not self.month_end_processed and (is_month_end or is_month_start):
            # Define the window for daily momentum calculation
            # We need 3 full days of data before the month boundary and 3 full days after
            # 72 bars = 3 days * 24 hours/day
            
            # Check for sufficient data for momentum calculation
            if len(closes) < 72: # 3 days * 24 hours/day
                return None

            # Calculate daily momentum leading up to month-end
            # Daily close 3 days ago, 2 days ago, 1 day ago
            # For simplicity, we'll use the close of the same hour from prior days
            # This is an approximation of daily closes
            
            # Find the closes for the "daily" momentum.
            # We need to look back 3 full days from the current bar's position.
            # So, we need closes from (current_bar_index - 24*3) to (current_bar_index - 24*1)
            
            # To get a daily close, we can simplify by taking the close of a specific hour
            # or the last close of the day before the day rolls over.
            # For this strategy, let's simplify and use the close of the current hour on previous days.

            # We need to ensure we have enough bars before the month-end/start.
            # This strategy is designed to trigger ON the month-end/start, but needs prior data.
            # So, we'll calculate the momentum based on the last 3 * 24 bars.
            
            # For simplicity, let's use the average price movement over the last 3 days (72 bars)
            # as a proxy for "decisive daily direction".
            
            momentum_period = 72 # 3 days * 24 hours/day
            
            # Long-term SMA for trend context
            long_sma_period = 200 # Roughly 8 days
            if len(closes) < long_sma_period:
                return None
            
            long_sma = self._sma(closes, long_sma_period)
            if long_sma is None:
                return None

            price_change = closes[-1] - closes[-momentum_period]
            
            # Threshold for "decisive" momentum (e.g., 0.5% over 3 days)
            decisive_threshold_pct = 0.005 # 0.5%
            
            # Only trade around certain hours to avoid noise
            if current_hour not in [0, 1, 2, 22, 23]: # Trade near daily close/open
                return None

            if ctx.has_position():
                return None

            signal_confidence = 0.7
            
            # Long condition: Strong positive momentum leading into month-end/start, price above long SMA
            if price_change / closes[-momentum_period] > decisive_threshold_pct and closes[-1] > long_sma:
                self.last_trade_bar = ctx.bar_index
                self.month_end_processed = True
                return ctx.signal(
                    "long",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "btc_month_turn_bullish_momentum",
                        "price_change_pct": price_change / closes[-momentum_period],
                        "current_price": closes[-1],
                        "long_sma": long_sma,
                        "timestamp": ctx.bar.timestamp.isoformat(),
                        "is_month_end": is_month_end,
                        "is_month_start": is_month_start,
                    }
                )
            
            # Short condition: Strong negative momentum leading into month-end/start, price below long SMA
            elif price_change / closes[-momentum_period] < -decisive_threshold_pct and closes[-1] < long_sma:
                self.last_trade_bar = ctx.bar_index
                self.month_end_processed = True
                return ctx.signal(
                    "short",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "btc_month_turn_bearish_momentum",
                        "price_change_pct": price_change / closes[-momentum_period],
                        "current_price": closes[-1],
                        "long_sma": long_sma,
                        "timestamp": ctx.bar.timestamp.isoformat(),
                        "is_month_end": is_month_end,
                        "is_month_start": is_month_start,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy, but could be added here.
        # For example, adjusting confidence based on recent win/loss rate.
        pass