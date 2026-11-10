from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFearStreakETH(Strategy):
    METADATA = {
        "name": "SentimentFearStreakETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_threshold = 30  # Fear & Greed index below this is considered "fear"
        self.fear_streak_length = 3 # Number of consecutive days of fear to trigger
        self.stabilization_period = 5 # Bars to check for stabilization after fear streak
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours for 1h bars)
        self.last_trade_bar = -self.cooldown_bars

        # Store daily FGI to detect streaks
        self.daily_fgi_history = []
        self.last_fgi_day = -1
        self.current_fear_streak = 0
        self.in_fear_streak = False

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Update daily FGI history
        current_day = ctx.bar.timestamp.day
        if current_day != self.last_fgi_day:
            fgi = ctx.features.get("fear_greed_index", 50)
            self.daily_fgi_history.append(fgi)
            if len(self.daily_fgi_history) > self.fear_streak_length + 5: # Keep a few extra
                self.daily_fgi_history.pop(0)
            self.last_fgi_day = current_day

            # Check for fear streak
            if len(self.daily_fgi_history) >= self.fear_streak_length:
                recent_fgis = self.daily_fgi_history[-self.fear_streak_length:]
                if all(f <= self.fear_threshold for f in recent_fgis):
                    if not self.in_fear_streak:
                        self.in_fear_streak = True
                        self.current_fear_streak = self.fear_streak_length
                    else:
                        self.current_fear_streak += 1 # Extend streak
                else:
                    self.in_fear_streak = False
                    self.current_fear_streak = 0
            else:
                self.in_fear_streak = False
                self.current_fear_streak = 0

        # Only consider long entries
        if ctx.has_position() and ctx.position_direction() == "long":
            return None # Hold existing long position

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: Long only
        if self.in_fear_streak and self.current_fear_streak >= self.fear_streak_length:
            # Check for price stabilization after fear streak
            # A simple stabilization: close is above the open for the last bar,
            # and the last N bars have relatively low volatility (e.g., small ATR)
            # and price is not making new lows
            if len(closes) < self.stabilization_period + 1:
                return None

            current_close = ctx.bar.close
            current_open = ctx.bar.open
            
            # Condition 1: Current bar is bullish (close > open)
            is_bullish_bar = current_close > current_open

            # Condition 2: Price is not making a new low within the stabilization period
            # and has moved above the low of the last fear-streak bar
            lows = ctx.lows(self.stabilization_period + 1)
            
            # Simple stabilization: current close is above the average of the last few closes
            # indicating a potential bounce or consolidation after a dip.
            sma_short = self._sma(closes, self.stabilization_period)
            if sma_short is None:
                return None
            
            is_stabilizing_price = current_close > sma_short

            # Combine conditions for entry
            if is_bullish_bar and is_stabilizing_price:
                # Confidence scales with how long the fear streak has been
                confidence = min(0.5 + (self.current_fear_streak - self.fear_streak_length) * 0.1, 0.9)
                
                # Reset fear streak to avoid re-triggering immediately
                self.in_fear_streak = False 
                self.current_fear_streak = 0 # Ensures we wait for a new streak to form

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fear_streak_stabilization_long",
                        "fear_greed_index": ctx.features.get("fear_greed_index", 50),
                        "fear_streak_length_days": self.current_fear_streak,
                        "current_close": current_close,
                        "sma_stabilization": sma_short,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index