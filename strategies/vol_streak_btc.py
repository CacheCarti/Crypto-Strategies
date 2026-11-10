from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityStreakFade(Strategy):
    METADATA = {
        "name": "Volatility Streak Fade BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for 4-hour holds on average
        "warmup_bars": 50,  # Need enough bars for ATR and streak detection
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.streak_length = 3  # Number of consecutive bars showing "continuation"
        self.streak_atr_multiplier = 1.5 # How many ATRs price must move for a "continuation"
        self.cooldown_bars = 12  # Cooldown period after an entry/exit
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade if conditions met

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None or current_atr == 0:
            return None

        # Detect extreme same-direction streak
        # A "continuation" is defined as a bar closing significantly higher/lower than its open,
        # indicating strong directional momentum, relative to ATR.
        
        long_streak_count = 0
        short_streak_count = 0

        # Look back `streak_length` bars
        for i in range(1, self.streak_length + 1): # Check bars up to and including the most recent one
            bar_close = closes[-i]
            bar_open = ctx.opens(self.warmup_bars)[-i]
            
            # Check for significant upward movement
            if bar_close - bar_open > self.streak_atr_multiplier * current_atr:
                long_streak_count += 1
                short_streak_count = 0 # Reset opposite streak
            # Check for significant downward movement
            elif bar_open - bar_close > self.streak_atr_multiplier * current_atr:
                short_streak_count += 1
                long_streak_count = 0 # Reset opposite streak
            else:
                # If neither strong up nor strong down, break the streak
                long_streak_count = 0
                short_streak_count = 0
            
            # We only care about the most recent full streak, so if we find a break, stop
            if long_streak_count == 0 and short_streak_count == 0 and i > 1:
                break


        signal_direction = None
        confidence = 0.0
        reason = ""

        # Fade a failed continuation after a strong long streak
        if long_streak_count >= self.streak_length:
            # The streak was confirmed on `closes[-1]`. We are looking for a *failure* on the *next* bar.
            # So, we need to check the current bar (closes[-1]) against the previous bar (closes[-2]).
            # This logic means the streak is completed on the bar *before* the current bar.
            # The current bar (ctx.bar.close) is the "failed continuation" attempt.

            # If the current bar closes below the previous bar's close (after a long streak)
            if ctx.bar.close < closes[-2]: # Fading the upward momentum
                if not ctx.has_position():
                    signal_direction = "short"
                    confidence = 0.65
                    reason = "failed_long_continuation_fade"
        
        # Fade a failed continuation after a strong short streak
        elif short_streak_count >= self.streak_length:
            # If the current bar closes above the previous bar's close (after a short streak)
            if ctx.bar.close > closes[-2]: # Fading the downward momentum
                if not ctx.has_position():
                    signal_direction = "long"
                    confidence = 0.65
                    reason = "failed_short_continuation_fade"

        if ctx.has_position():
            # If we have a position and conditions flip, consider flipping
            if ctx.position_direction() == "long" and signal_direction == "short":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.7, metadata={"reason": "flip_long_to_short", "current_atr": current_atr, "close": ctx.bar.close})
            elif ctx.position_direction() == "short" and signal_direction == "long":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.7, metadata={"reason": "flip_short_to_long", "current_atr": current_atr, "close": ctx.bar.close})
            
            # If we have a position and no new signal, or same signal, hold
            return None # Hold current position

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_atr": current_atr,
                    "long_streak_count": long_streak_count,
                    "short_streak_count": short_streak_count,
                    "close": ctx.bar.close,
                    "previous_close": closes[-2]
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        # Optional: adaptive logic based on position outcome
        # For this strategy, a simple cooldown is sufficient.
        pass