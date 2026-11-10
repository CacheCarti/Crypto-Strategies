from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ExhaustionFade(Strategy):
    METADATA = {
        "name": "Exhaustion Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Aim for a few hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_period = 5
        self.lookback_period = 20
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period)
        opens = ctx.opens(self.lookback_period)
        highs = ctx.highs(self.lookback_period)
        lows = ctx.lows(self.lookback_period)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown period
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        prev_close = closes[-2] if len(closes) >= 2 else None

        if prev_close is None:
            return None

        # Calculate consecutive up/down bars
        up_streak = 0
        down_streak = 0
        for i in range(1, min(len(closes), self.streak_period + 1)):
            if closes[-i] > opens[-i]: # Green candle
                up_streak += 1
                down_streak = 0
            elif closes[-i] < opens[-i]: # Red candle
                down_streak += 1
                up_streak = 0
            else: # Doji or flat, reset
                up_streak = 0
                down_streak = 0
            
            if up_streak >= self.streak_period or down_streak >= self.streak_period:
                break # Streak found

        signal_direction = None
        confidence = 0.0
        reason = ""
        
        # Mean Reversion Target (simple SMA for now)
        sma_target = self._sma(closes, self.lookback_period)
        
        # Check for exhaustion fade entry
        if up_streak >= self.streak_period and sma_target is not None:
            # After a strong up streak, look for a failed continuation (red candle) to short
            if current_close < current_open and current_close < prev_close: # Current bar is red and closes lower than previous
                # Only short if price is above SMA, indicating overextension
                if current_close > sma_target:
                    signal_direction = "short"
                    confidence = 0.6 + ctx.rng.uniform(0, 0.2) # Add some randomness
                    reason = "exhaustion_fade_short_after_up_streak"
                    
        elif down_streak >= self.streak_period and sma_target is not None:
            # After a strong down streak, look for a failed continuation (green candle) to long
            if current_close > current_open and current_close > prev_close: # Current bar is green and closes higher than previous
                # Only long if price is below SMA, indicating overextension
                if current_close < sma_target:
                    signal_direction = "long"
                    confidence = 0.6 + ctx.rng.uniform(0, 0.2) # Add some randomness
                    reason = "exhaustion_fade_long_after_down_streak"
        
        # Exit logic for existing position
        if ctx.has_position():
            pos = ctx.position
            entry_price = pos["entry_price"]
            
            # If we are long, and price crosses below SMA, consider exiting
            if pos["direction"] == "long" and sma_target is not None and current_close < sma_target:
                if current_close > entry_price: # Only exit for profit
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "mean_reversion_long_exit_sma_cross_profit",
                        "current_close": current_close,
                        "sma_target": sma_target
                    })
            # If we are short, and price crosses above SMA, consider exiting
            elif pos["direction"] == "short" and sma_target is not None and current_close > sma_target:
                if current_close < entry_price: # Only exit for profit
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "mean_reversion_short_exit_sma_cross_profit",
                        "current_close": current_close,
                        "sma_target": sma_target
                    })
            
            # Hold if no exit condition met
            return ctx.signal("hold")

        # Entry signal
        if signal_direction:
            # Add a filter based on Fear & Greed Index to avoid trading in extreme fear/greed
            fear_greed = ctx.features.get("fear_greed_index", 50)
            if fear_greed < 25 or fear_greed > 75: # Avoid extreme market sentiment
                return None

            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "up_streak": up_streak,
                    "down_streak": down_streak,
                    "current_close": current_close,
                    "sma_target": sma_target,
                    "fear_greed_index": fear_greed
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close or adapt strategy based on performance
        pass