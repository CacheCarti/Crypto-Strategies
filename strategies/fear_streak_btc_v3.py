from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedRecovery(Strategy):
    METADATA = {
        "name": "FearGreedRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 21600, # 6 hours for a 1h bar strategy
        "warmup_bars": 72, # Need enough bars for 3 days of Fear & Greed, plus EMA
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_greed_threshold_entry = 25
        self.fear_greed_threshold_exit = 45
        self.min_streak_bars = 3 * 24 # 3 days of hourly bars for fear streak
        self.ema_period = 20
        self.cooldown_bars = 12 # 12 hours cooldown after a trade
        self.last_signal_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trading

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        current_fg_index = ctx.features.get("fear_greed_index", 50)
        
        # Check for fear streak (daily feature, so it will be constant for 24 bars)
        # We need to look back multiple days, so check if the FG index was below threshold for previous days.
        # Since FG is daily, we check the value at specific past bars (e.g., 24, 48, 72 bars ago)
        # to see if it was below the threshold for a streak.
        
        # To avoid re-evaluating the same daily FG index multiple times for the same day,
        # we'll look at the FG index at the start of the previous days.
        # We'll use the FG index from 24, 48, 72 hours ago to represent previous days' readings.
        
        if ctx.bar_index < self.min_streak_bars:
            return None # Not enough history for a 3-day streak

        # Check for a streak of low Fear & Greed index (e.g., 3 consecutive days)
        # We look at the FG index at the same hour of the day for the last 3 days.
        # The feature is daily, so ctx.features.get("fear_greed_index", 50) will return the same value
        # for all bars within a 24-hour period.
        # We need to detect the *transition* from a streak to a recovery.

        # Let's track the FG index for the current day and previous days
        fg_today = ctx.features.get("fear_greed_index", 50)
        fg_yesterday = ctx.features.get("fear_greed_index", 50, offset_bars=24)
        fg_two_days_ago = ctx.features.get("fear_greed_index", 50, offset_bars=48)
        
        is_in_fear_streak = (fg_yesterday < self.fear_greed_threshold_entry and
                             fg_two_days_ago < self.fear_greed_threshold_entry)

        # Entry logic: if we were in a fear streak, and today is the first day the FG index recovers (not necessarily above threshold yet)
        # This means fg_yesterday and fg_two_days_ago were low, but fg_today is higher than yesterday.
        # And we need to be below the entry threshold for the current day to consider it a "recovery day start"
        
        if (is_in_fear_streak and 
            fg_today > fg_yesterday and 
            fg_today < self.fear_greed_threshold_exit and # Still in a general "fear" zone, but recovering
            not ctx.has_position()):
            
            # Additional price-based confirmation: price above a short-term EMA
            ema = self._ema(closes, self.ema_period)
            if ema is None:
                return None
            
            current_close = ctx.bar.close
            
            if current_close > ema:
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "fear_greed_recovery_buy",
                        "fear_greed_today": fg_today,
                        "fear_greed_yesterday": fg_yesterday,
                        "fear_greed_2_days_ago": fg_two_days_ago,
                        "current_close": current_close,
                        "ema": ema,
                        "ema_period": self.ema_period
                    }
                )

        # Exit logic: if we have a position and the fear_greed_index recovers above the exit threshold
        if ctx.has_position() and ctx.position_direction() == "long":
            if current_fg_index > self.fear_greed_threshold_exit:
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "fear_greed_recovery_exit",
                        "fear_greed_current": current_fg_index,
                        "fear_greed_threshold_exit": self.fear_greed_threshold_exit
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after any position close
        self.last_signal_bar_index = ctx.bar_index
        # Optional: adaptive sizing based on position['return_bps']
        # For this strategy, we keep it simple and just reset cooldown.
        pass