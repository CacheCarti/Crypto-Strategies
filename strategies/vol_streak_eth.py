from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityStreakFade(Strategy):
    METADATA = {
        "name": "Volatility Streak Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this pure price-action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_period = 3 # Number of consecutive bars for a streak
        self.continuation_period = 1 # Number of bars to check for failed continuation
        self.cooldown_bars = 10 # Bars to wait after a trade before new entry
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        # Volatility check: using ATR
        self.atr_period = 14
        self.atr_multiplier = 1.5 # How many ATRs define "high volatility"
        self.long_term_atr_period = 48 # For context

        # State variables to track streaks
        self.current_streak_direction = None # "long", "short", or None
        self.current_streak_count = 0
        self.last_bar_close = None

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Calculate ATR for volatility filter
        atr_val = self._atr(highs, lows, closes, self.atr_period)
        long_term_atr_val = self._atr(highs, lows, closes, self.long_term_atr_period)

        if atr_val is None or long_term_atr_val is None:
            return None

        # Volatility filter: only consider signals if current volatility is above average
        # This helps ensure we're looking at "event-structure" periods
        if atr_val < long_term_atr_val * 0.8: # Require somewhat elevated volatility
            self.current_streak_direction = None # Reset streak if volatility drops
            self.current_streak_count = 0
            self.last_bar_close = current_close
            return None

        # Update streak tracking
        if self.last_bar_close is not None:
            if current_close > self.last_bar_close:
                # Up bar
                if self.current_streak_direction == "long":
                    self.current_streak_count += 1
                else:
                    self.current_streak_direction = "long"
                    self.current_streak_count = 1
            elif current_close < self.last_bar_close:
                # Down bar
                if self.current_streak_direction == "short":
                    self.current_streak_count += 1
                else:
                    self.current_streak_direction = "short"
                    self.current_streak_count = 1
            else:
                # Doji/flat bar resets streak
                self.current_streak_direction = None
                self.current_streak_count = 0
        self.last_bar_close = current_close

        # Strategy logic: Fade the first failed continuation after an extreme same-direction streak
        signal_direction = None
        reason = ""
        confidence = 0.0

        if self.current_streak_count >= self.streak_period:
            # We have an established streak. Now check for failed continuation.
            # A failed continuation means the current bar moves against the streak direction.

            # Check for a "long" streak followed by a "failed continuation" (down bar)
            if self.current_streak_direction == "long" and current_close < closes[-2]: # current bar is down
                # The previous bar was the end of a streak, and this bar is the failure
                if not ctx.has_position():
                    signal_direction = "short"
                    reason = "long_streak_fade_failed_continuation"
                    confidence = min(0.7, 0.4 + (self.current_streak_count / 10.0)) # Higher confidence for longer streaks
                    self.last_trade_bar = ctx.bar_index

            # Check for a "short" streak followed by a "failed continuation" (up bar)
            elif self.current_streak_direction == "short" and current_close > closes[-2]: # current bar is up
                # The previous bar was the end of a streak, and this bar is the failure
                if not ctx.has_position():
                    signal_direction = "long"
                    reason = "short_streak_fade_failed_continuation"
                    confidence = min(0.7, 0.4 + (self.current_streak_count / 10.0)) # Higher confidence for longer streaks
                    self.last_trade_bar = ctx.bar_index

            # Reset streak after a potential signal generation to prevent continuous firing
            self.current_streak_direction = None
            self.current_streak_count = 0

        # Exit logic
        if ctx.has_position():
            # If we detect a strong opposing streak while in position, consider exiting
            if ctx.position_direction() == "long" and self.current_streak_direction == "short" and self.current_streak_count >= self.streak_period:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "exit_opposing_streak_formed", "streak_count": self.current_streak_count})
            if ctx.position_direction() == "short" and self.current_streak_direction == "long" and self.current_streak_count >= self.streak_period:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "exit_opposing_streak_formed", "streak_count": self.current_streak_count})
            return ctx.signal("hold") # Hold existing position if no new signal or exit condition met

        if signal_direction:
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "streak_period": self.streak_period,
                    "continuation_period": self.continuation_period,
                    "streak_count_at_signal": self.current_streak_count,
                    "current_close": current_close,
                    "atr_val": atr_val,
                    "long_term_atr": long_term_atr_val,
                    "regime": ctx.regime,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close to ensure next signal respects it
        self.last_trade_bar = ctx.bar_index
        # Log for debugging/analysis
        # print(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps, held {position['bars_held']} bars.")