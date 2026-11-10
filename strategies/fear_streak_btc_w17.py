from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentStreakRecovery(Strategy):
    METADATA = {
        "name": "Sentiment Streak Recovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 259200,  # ~3 days
        "warmup_bars": 72,  # 3 days of 1-hour bars for F&G index streak and price momentum
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_greed_streak = 0
        self.last_fear_greed_index = None
        self.cooldown_bars = 0
        self.cooldown_duration = 24 # Cooldown for 24 hours after a trade
        self.entry_price = 0.0

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if self.cooldown_bars > 0:
            self.cooldown_bars -= 1
            return None

        current_fg_index = ctx.features.get("fear_greed_index", 50)
        
        # Detect Fear & Greed streak and recovery
        if self.last_fear_greed_index is not None:
            if current_fg_index < 25 and self.last_fear_greed_index < 25:
                self.fear_greed_streak += 1
            elif current_fg_index >= 25 and self.last_fear_greed_index < 25 and self.fear_greed_streak >= 2:
                # First up day after a streak of at least 2 days below 25
                # Check for price momentum confirmation
                sma_24 = self._sma(closes, 24) # 24-hour SMA
                if sma_24 is None:
                    return None
                
                # Entry conditions:
                # 1. Fear & Greed index recovering after a streak of deep fear
                # 2. Price is above its short-term moving average (momentum confirmation)
                # 3. Not currently in a position
                if not ctx.has_position() and closes[-1] > sma_24:
                    self.cooldown_bars = self.cooldown_duration
                    self.entry_price = ctx.bar.close
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "fear_greed_streak_recovery_with_price_momentum",
                            "fear_greed_index": current_fg_index,
                            "previous_fg_index": self.last_fear_greed_index,
                            "fear_greed_streak_days": self.fear_greed_streak,
                            "current_close": closes[-1],
                            "sma_24": sma_24,
                        }
                    )
            elif current_fg_index > 45:
                # Reset streak if index recovers significantly
                self.fear_greed_streak = 0
            
            # Reset streak if current index is above 25 but previous was also above 25
            if current_fg_index >= 25 and self.last_fear_greed_index >= 25:
                self.fear_greed_streak = 0

        self.last_fear_greed_index = current_fg_index

        # Exit logic for existing position
        if ctx.has_position() and ctx.position_direction() == "long":
            # Exit if Fear & Greed index recovers above 45
            if current_fg_index > 45 and self.last_fear_greed_index <= 45: # Transition above 45
                self.cooldown_bars = self.cooldown_duration
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "fear_greed_index_recovered_above_45",
                        "fear_greed_index": current_fg_index,
                        "entry_price": self.entry_price,
                        "exit_price": ctx.bar.close,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close, regardless of how it closed
        self.cooldown_bars = self.cooldown_duration
        # Optional: log position details or adapt future behavior
        # print(f"Position closed: {position['direction']} at {position['exit_price']} with {position['return_bps']} bps return.")