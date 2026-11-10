from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolStreakFade(Strategy):
    METADATA = {
        "name": "SolVolStreakFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": [], # No specific features needed, using OHLCV + regime
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_period = 10  # Number of bars to check for a streak
        self.continuation_period = 3 # Number of bars to check for failed continuation
        self.min_streak_bars = 5 # Minimum bars for a streak to be considered
        self.entry_cooldown_bars = 10 # Cooldown after an entry signal
        self.last_signal_bar = -self.entry_cooldown_bars # Initialize to allow immediate first trade

        # Thresholds for 'significant' movement
        self.movement_threshold_pct = 0.005 # 0.5% movement per bar
        self.reversal_threshold_pct = 0.003 # 0.3% reversal movement

        self.last_streak_direction = None # To detect streak transitions
        self.last_streak_end_bar = -self.entry_cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.streak_period + self.continuation_period + 1)
        opens = ctx.opens(self.streak_period + self.continuation_period + 1)
        highs = ctx.highs(self.streak_period + self.continuation_period + 1)
        lows = ctx.lows(self.streak_period + self.continuation_period + 1)

        if len(closes) < self.streak_period + self.continuation_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar + self.entry_cooldown_bars:
            return None

        # Calculate recent price changes
        price_changes = [(closes[i] - opens[i]) / opens[i] for i in range(len(closes))]

        # Detect strong streak
        current_streak_direction = None
        current_streak_length = 0
        for i in range(1, self.streak_period + 1):
            idx = -(i + self.continuation_period)
            change = price_changes[idx]
            if change > self.movement_threshold_pct:
                if current_streak_direction == "down": # Streak changed direction, reset
                    current_streak_length = 0
                    break
                current_streak_direction = "up"
                current_streak_length += 1
            elif change < -self.movement_threshold_pct:
                if current_streak_direction == "up": # Streak changed direction, reset
                    current_streak_length = 0
                    break
                current_streak_direction = "down"
                current_streak_length += 1
            else: # Neutral bar, streak broken
                current_streak_length = 0
                break

        # Only consider streaks that meet minimum length
        if current_streak_length < self.min_streak_bars:
            self.last_streak_direction = None # Reset streak detection
            return None

        # Ensure we are not already in a position
        if ctx.has_position():
            return None

        # Check for failed continuation after the streak
        # We need a clear transition from streak detection to continuation check
        # This means the current streak was previously detected and now we're past its "end"
        if self.last_streak_direction == current_streak_direction and \
           ctx.bar_index < self.last_streak_end_bar + self.continuation_period:
            # Still within the window of checking for continuation of the *previous* streak
            # Or the streak just started and we're detecting it.
            # We want to fade the *first failed continuation* *after* a streak.
            # So, we track the end of a confirmed streak, and then check the bars immediately following it.
            pass # We only update last_streak_direction on new streak detection

        # Determine if the current streak is a *new* streak or continuation of old
        is_new_streak = (self.last_streak_direction != current_streak_direction) or \
                        (ctx.bar_index >= self.last_streak_end_bar + self.continuation_period)

        if is_new_streak:
            self.last_streak_direction = current_streak_direction
            self.last_streak_end_bar = ctx.bar_index
            return None # Wait for continuation to fail after a new streak is established

        # Now, check the continuation bars immediately after the detected streak
        # The streak ended at closes[-(self.continuation_period + 1)]
        # We look at closes[-self.continuation_period] to closes[-1]
        
        failed_continuation = False
        if current_streak_direction == "up":
            # For an upward streak, a failed continuation means prices moved down significantly
            for i in range(self.continuation_period):
                idx = -(self.continuation_period - i)
                if price_changes[idx] < -self.reversal_threshold_pct:
                    failed_continuation = True
                    break
        elif current_streak_direction == "down":
            # For a downward streak, a failed continuation means prices moved up significantly
            for i in range(self.continuation_period):
                idx = -(self.continuation_period - i)
                if price_changes[idx] > self.reversal_threshold_pct:
                    failed_continuation = True
                    break

        if failed_continuation:
            # Check for regime filter: avoid trading in crisis/high vol for fades
            if ctx.market.get("regime", "NORMAL") in ["CRISIS", "HIGH_VOL"]:
                return None

            # Generate signal to fade the streak
            confidence = 0.7 + (0.3 * (current_streak_length / self.streak_period)) # Higher confidence for longer streaks
            
            if current_streak_direction == "up":
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("short", confidence=confidence, metadata={
                    "reason": "fade_failed_up_continuation",
                    "streak_direction": "up",
                    "streak_length": current_streak_length,
                    "continuation_fail_bar_change_pct": price_changes[-(self.continuation_period)],
                    "regime": ctx.market.get("regime", "NORMAL")
                })
            elif current_streak_direction == "down":
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("long", confidence=confidence, metadata={
                    "reason": "fade_failed_down_continuation",
                    "streak_direction": "down",
                    "streak_length": current_streak_length,
                    "continuation_fail_bar_change_pct": price_changes[-(self.continuation_period)],
                    "regime": ctx.market.get("regime", "NORMAL")
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just reset last signal bar for cooldown
        pass