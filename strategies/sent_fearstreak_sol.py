from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFearStreakSOL(Strategy):
    METADATA = {
        "name": "Sentiment Fear Streak SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours, slightly longer than typical swing
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_period = 5 # Number of consecutive low FGI readings
        self.fear_threshold = 30 # FGI value considered "fear"
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_signal_bar = -self.cooldown_bars
        self.ema_period = 20 # For price stabilization check
        self.min_volume_ratio = 1.0 # Minimum volume relative to avg volume
        
        self.last_fgi_reading_bar = -1
        self.fear_streak_count = 0

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars + 1:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_fgi = ctx.features.get("fear_greed_index", 50)

        # Update fear streak counter - FGI is daily, so only update once per day
        if ctx.bar.timestamp.day != self.last_fgi_reading_bar:
            if current_fgi <= self.fear_threshold:
                self.fear_streak_count += 1
            else:
                self.fear_streak_count = 0
            self.last_fgi_reading_bar = ctx.bar.timestamp.day

        # Condition 1: Fear streak
        fear_streak_active = self.fear_streak_count >= self.fear_period

        # Condition 2: Price stabilization (simple EMA cross above)
        ema = self._ema(closes[:-1], self.ema_period) # EMA of previous closes
        if ema is None:
            return None
        
        current_close = closes[-1]
        previous_close = closes[-2]

        # Check for first stabilization bar after fear streak
        # This means previous close was below EMA, and current close is above EMA
        price_stabilized = (previous_close < ema and current_close > ema)

        # Condition 3: Volume confirmation (optional but good for robustness)
        avg_volume = self._sma(volumes[:-1], self.ema_period) # Avg volume of previous bars
        volume_confirm = True
        if avg_volume is not None and avg_volume > 0:
            volume_confirm = (volumes[-1] > avg_volume * self.min_volume_ratio)

        if not ctx.has_position():
            if fear_streak_active and price_stabilized and volume_confirm:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fear_streak_ema_stabilization_long",
                        "fear_streak_count": self.fear_streak_count,
                        "current_fgi": current_fgi,
                        "ema_period": self.ema_period,
                        "ema_value": ema,
                        "current_close": current_close,
                        "previous_close": previous_close,
                        "current_volume": volumes[-1],
                        "avg_volume": avg_volume,
                    }
                )
        elif ctx.position_direction() == "long":
            # Optional: Exit if FGI goes back to greed/neutral or price breaks down
            # For now, let TP/SL and horizon manage exits.
            pass

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset fear streak count if the trade was profitable to avoid re-entering too soon on same fear streak
        # Or, just use the cooldown_bars to manage re-entry frequency.
        # For this strategy, cooldown_bars is sufficient.
        pass