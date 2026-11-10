from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MonthEndFlow(Strategy):
    METADATA = {
        "name": "Month End Flow",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 24 * 2,  # Approximately 2 days hold
        "warmup_bars": 50,  # Need enough bars for 20-period indicators
        "required_features": ["eth_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period_fast = 12
        self.ema_period_slow = 26
        self.vol_period = 20
        self.cooldown_bars = 48  # Cooldown for 2 days after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1) # Need one extra for daily change
        volumes = ctx.volumes(self.vol_period)
        
        if len(closes) < self.warmup_bars + 1 or len(volumes) < self.vol_period:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_month = ctx.bar.timestamp.month
        current_day = ctx.bar.timestamp.day
        current_year = ctx.bar.timestamp.year

        # Determine last day of month
        next_month = current_month + 1
        next_year = current_year
        if next_month > 12:
            next_month = 1
            next_year += 1
        
        # Calculate last day of current month
        # This is a bit tricky without datetime.timedelta or calendar module
        # A simpler way is to find the day before the 1st of next month
        if next_month == 1: # January
            last_day_of_month = 31 # December
        elif next_month == 2: # February
            last_day_of_month = 31 # January
        elif next_month == 3: # March
            last_day_of_month = 28 if next_year % 4 != 0 or (next_year % 100 == 0 and next_year % 400 != 0) else 29 # Feb
        elif next_month == 4: # April
            last_day_of_month = 31 # March
        elif next_month == 5: # May
            last_day_of_month = 30 # April
        elif next_month == 6: # June
            last_day_of_month = 31 # May
        elif next_month == 7: # July
            last_day_of_month = 30 # June
        elif next_month == 8: # August
            last_day_of_month = 31 # July
        elif next_month == 9: # September
            last_day_of_month = 31 # August
        elif next_month == 10: # October
            last_day_of_month = 30 # September
        elif next_month == 11: # November
            last_day_of_month = 31 # October
        elif next_month == 12: # December
            last_day_of_month = 30 # November
        else:
            last_day_of_month = 31 # Default for safety

        is_last_day_of_month = (current_day == last_day_of_month)
        is_second_to_last_day = (current_day == last_day_of_month - 1)

        # Entry logic: Last two days of the month
        if not ctx.has_position() and (is_last_day_of_month or is_second_to_last_day):
            
            # Daily move calculation (using previous day's close for simplicity)
            # We need daily closes, but we only have hourly.
            # Let's approximate daily move by comparing current close to close 24 bars ago.
            if len(closes) < 25: # Need at least 24 hours + current bar
                return None
            
            prev_day_close = closes[-25] # Close from 24 hours ago
            current_close = ctx.bar.close
            
            daily_price_change_pct = ((current_close - prev_day_close) / prev_day_close) * 100 if prev_day_close != 0 else 0.0

            # Volatility filter: Only trade if market isn't too calm or too volatile
            regime = ctx.market.get("regime", "NORMAL")
            if regime == "CALM" or regime == "CRISIS" or regime == "MELTDOWN":
                return None

            # Trend filter: Only trade with the dominant trend
            trend_regime = ctx.market.get("trend_regime", "neutral")
            
            # Volume confirmation: current volume vs average volume
            avg_volume = self._sma(volumes, self.vol_period)
            if avg_volume is None or avg_volume == 0:
                return None
            volume_ratio = ctx.bar.volume / avg_volume

            # Decisive daily move threshold (e.g., > 1% for long, < -1% for short)
            decisive_move_threshold = 0.5  # Adjust as needed, e.g., 0.5% or 1%
            
            # Volume confirmation threshold (e.g., 1.2x average volume)
            volume_confirm_threshold = 1.2

            # Fear & Greed Index as a sentiment filter
            fear_greed_index = ctx.features.get("fear_greed_index", 50)

            signal_direction = None
            confidence = 0.0
            reason = ""

            # Long entry conditions
            if daily_price_change_pct > decisive_move_threshold and \
               volume_ratio > volume_confirm_threshold and \
               trend_regime == "bull" and \
               fear_greed_index > 40: # Not in extreme fear
                signal_direction = "long"
                confidence = min(1.0, 0.5 + (daily_price_change_pct / 2.0) + (volume_ratio - 1.0) / 2.0)
                reason = "month_end_long_flow_bullish_confirm"

            # Short entry conditions
            elif daily_price_change_pct < -decisive_move_threshold and \
                 volume_ratio > volume_confirm_threshold and \
                 trend_regime == "bear" and \
                 fear_greed_index < 60: # Not in extreme greed
                signal_direction = "short"
                confidence = min(1.0, 0.5 + (abs(daily_price_change_pct) / 2.0) + (volume_ratio - 1.0) / 2.0)
                reason = "month_end_short_flow_bearish_confirm"
            
            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "daily_price_change_pct": daily_price_change_pct,
                        "volume_ratio": volume_ratio,
                        "trend_regime": trend_regime,
                        "fear_greed_index": fear_greed_index,
                        "current_day": current_day,
                        "last_day_of_month": last_day_of_month,
                        "price": current_close
                    }
                )

        # Exit logic: First day of the new month
        if ctx.has_position():
            # Check if it's the first day of the new month
            # We're already checking for current_day == 1
            if current_day == 1 and ctx.position_direction() is not None:
                self.last_trade_bar = ctx.bar_index # Reset cooldown on exit
                return ctx.signal(
                    "flat",
                    confidence=0.8, # High confidence to exit
                    metadata={
                        "reason": "month_start_exit",
                        "current_day": current_day,
                        "current_month": current_month,
                        "entry_month": ctx.position["entry_timestamp"].month if "entry_timestamp" in ctx.position else "N/A"
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive sizing or state reset needed beyond cooldown
        pass