from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedStreakRecovery(Strategy):
    METADATA = {
        "name": "Fear Greed Streak Recovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 700.0,
        "declared_hold_seconds": 24 * 3600, # Intended hold for multiple days
        "warmup_bars": 7 * 24, # Need enough bars for 7 days of FGI
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak_length = 3 # Minimum number of consecutive days FGI < 25
        self.fear_threshold = 25
        self.recovery_threshold = 45
        self.cooldown_bars = 48 # Cooldown for 2 days after a trade

        self.last_fgi_day = -1
        self.current_fgi_streak = 0 # Consecutive days FGI < fear_threshold
        self.fgi_values_for_day = [] # Store FGI values for the current day

        self.last_trade_bar = -self.cooldown_bars

    def _get_daily_fgi(self, ctx: BarContext) -> Optional[float]:
        # FGI is daily, so we need to track if a new day has started
        current_day = ctx.bar.timestamp.day
        current_fgi = ctx.features.get("fear_greed_index", 50)

        if current_day != self.last_fgi_day:
            # New day, reset daily FGI tracking
            self.last_fgi_day = current_day
            self.fgi_values_for_day = [current_fgi]
            return current_fgi # This is the first FGI for the new day
        else:
            # Same day, if FGI has changed, use the new one. Otherwise, it's a repeat.
            if not self.fgi_values_for_day or self.fgi_values_for_day[-1] != current_fgi:
                self.fgi_values_for_day.append(current_fgi)
                return current_fgi
        return None # No new daily FGI value

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Process daily Fear & Greed Index
        new_daily_fgi = self._get_daily_fgi(ctx)

        if new_daily_fgi is not None:
            if new_daily_fgi < self.fear_threshold:
                self.current_fgi_streak += 1
            else:
                self.current_fgi_streak = 0 # Reset streak if FGI recovers

        # Short-term EMA for entry trigger
        ema_short_period = 5
        ema_long_period = 20
        ema_short = self._sma(closes, ema_short_period) # Using SMA as a proxy for EMA for simplicity to fit line budget
        ema_long = self._sma(closes, ema_long_period)

        if ema_short is None or ema_long is None:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic:
        # 1. FGI streak below threshold for min_streak_length
        # 2. FGI has just recovered above the fear_threshold (first up day)
        # 3. Price confirmation: short EMA crosses above long EMA (bullish momentum)
        if not ctx.has_position():
            if self.current_fgi_streak >= self.min_streak_length and \
               new_daily_fgi is not None and new_daily_fgi >= self.fear_threshold and \
               closes[-1] > ema_long and closes[-2] <= ema_long: # Price crosses above long EMA
                
                # Check for a recent FGI below fear_threshold to confirm recovery
                # (This relies on new_daily_fgi being the first FGI above threshold after a streak)
                # We need to ensure the previous FGI was below the threshold to confirm "recovery"
                # This is implicitly handled by `new_daily_fgi is not None and new_daily_fgi >= self.fear_threshold`
                # and `self.current_fgi_streak >= self.min_streak_length`
                
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "fgi_streak_recovery_ema_cross_long",
                        "fear_greed_index": new_daily_fgi,
                        "fgi_streak_length": self.current_fgi_streak,
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "price": closes[-1],
                    }
                )

        # Exit logic:
        # 1. FGI recovers above a higher threshold
        # 2. Time-based exit (declared_hold_seconds handled by engine, but we can add FGI-based time exit too)
        if ctx.has_position() and ctx.position_direction() == "long":
            if new_daily_fgi is not None and new_daily_fgi >= self.recovery_threshold:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "fgi_recovery_exit",
                        "fear_greed_index": new_daily_fgi,
                        "fgi_recovery_threshold": self.recovery_threshold,
                        "entry_price": ctx.position["entry_price"],
                        "current_price": closes[-1],
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic needed for this strategy, just logging.
        # The cooldown is handled in on_bar.
        pass