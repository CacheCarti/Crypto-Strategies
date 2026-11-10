from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentStreakRecovery(Strategy):
    METADATA = {
        "name": "Sentiment Streak Recovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 12 * 3600,  # Aim for 12 hours to several days
        "warmup_bars": 7 * 24, # Need enough bars for 7 days of fear_greed_index readings
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_greed_streak_threshold = 25
        self.fear_greed_recovery_threshold = 45
        self.min_streak_length = 3 # Minimum number of consecutive days below threshold
        self.last_fear_greed_day = -1
        self.current_streak_length = 0
        self.fear_greed_history = [] # To store daily fear_greed_index
        self.entry_bar_index = -1
        self.cooldown_bars = 24 * 3 # Cooldown for 3 days after an entry or exit
        self.position_active = False

        # For price-based confirmation: simple moving average
        self.sma_period = 20
        self.sma_values = []

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close

        # Update SMA
        self.sma_values.append(current_price)
        if len(self.sma_values) > self.sma_period:
            self.sma_values.pop(0)
        current_sma = self._sma(self.sma_values, self.sma_period)

        # Handle daily Fear & Greed Index
        current_day = ctx.bar.timestamp.day
        if current_day != self.last_fear_greed_day:
            fgi = ctx.features.get("fear_greed_index", 50)
            self.fear_greed_history.append(fgi)
            if len(self.fear_greed_history) > self.min_streak_length + 1: # Keep enough history
                self.fear_greed_history.pop(0)

            # Check for fear streak
            if fgi < self.fear_greed_streak_threshold:
                self.current_streak_length += 1
            else:
                self.current_streak_length = 0 # Reset streak if above threshold

            self.last_fear_greed_day = current_day

        # Cooldown logic
        if self.entry_bar_index != -1 and ctx.bar_index - self.entry_bar_index < self.cooldown_bars:
            return None

        # Exit logic
        if ctx.has_position():
            self.position_active = True
            current_fgi = ctx.features.get("fear_greed_index", 50)
            entry_fgi = ctx.position["metadata"].get("entry_fgi", 50)

            # Exit if Fear & Greed recovers significantly
            if current_fgi > self.fear_greed_recovery_threshold and current_fgi > entry_fgi:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "fear_greed_recovered",
                        "current_fgi": current_fgi,
                        "entry_fgi": entry_fgi,
                        "recovery_threshold": self.fear_greed_recovery_threshold,
                        "price": current_price
                    }
                )
            return None

        # Entry logic
        if not ctx.has_position() and not self.position_active:
            # Require at least min_streak_length days of fear
            if self.current_streak_length >= self.min_streak_length:
                # Need at least two days of FGI history to detect an "up day"
                if len(self.fear_greed_history) >= 2:
                    # Check for "first up day" after streak
                    # This means the current day's FGI is higher than the previous day's FGI
                    # AND the previous day's FGI was below the streak threshold
                    last_fgi = self.fear_greed_history[-2] # FGI for the previous day
                    current_fgi = self.fear_greed_history[-1] # FGI for the current day

                    if current_fgi > last_fgi and last_fgi < self.fear_greed_streak_threshold:
                        # Price confirmation: current price must be above SMA
                        if current_sma is not None and current_price > current_sma:
                            self.entry_bar_index = ctx.bar_index
                            self.position_active = True # Set flag to prevent re-entry during cooldown
                            return ctx.signal(
                                "long",
                                confidence=0.75,
                                metadata={
                                    "reason": "sentiment_streak_recovery_long",
                                    "fear_greed_streak_length": self.current_streak_length,
                                    "current_fgi": current_fgi,
                                    "last_fgi": last_fgi,
                                    "current_price": current_price,
                                    "sma": current_sma,
                                    "entry_fgi": current_fgi # Store FGI at entry
                                }
                            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset the position_active flag after a position closes to allow new entries
        # after the cooldown period.
        self.position_active = False
        self.entry_bar_index = ctx.bar_index # Start cooldown after position closes
        # Log or adapt based on position outcome if needed
        # print(f"Position closed: {position['direction']} {position['return_bps']} bps, held for {position['bars_held']} bars")