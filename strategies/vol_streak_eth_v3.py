from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityStreakFade(Strategy):
    METADATA = {
        "name": "VolatilityStreakFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 3,  # Aim for a few hours hold
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_period = 10  # Number of bars to check for streak
        self.continuation_period = 3 # Number of bars to check for failed continuation
        self.cooldown_bars = 5   # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.streak_period + self.continuation_period + 1)
        if len(closes) < self.streak_period + self.continuation_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Determine if there's a strong streak
        up_streak_count = 0
        down_streak_count = 0
        for i in range(1, self.streak_period + 1):
            if closes[-i] > closes[-(i+1)]:
                up_streak_count += 1
            elif closes[-i] < closes[-(i+1)]:
                down_streak_count += 1

        is_up_streak = up_streak_count >= self.streak_period * 0.8
        is_down_streak = down_streak_count >= self.streak_period * 0.8

        # Check for failed continuation after the streak
        if is_up_streak and not ctx.has_position():
            # After an up streak, we expect continuation up. If it fails, we fade (short).
            # Check the next `continuation_period` bars.
            failed_continuation = True
            for i in range(1, self.continuation_period + 1):
                if closes[-i] > closes[-(i+1)]: # If price is still going up
                    failed_continuation = False
                    break
            
            if failed_continuation:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "failed_continuation_after_up_streak",
                        "streak_period": self.streak_period,
                        "up_streak_count": up_streak_count,
                        "continuation_period": self.continuation_period,
                        "current_price": ctx.bar.close,
                        "regime": ctx.regime,
                    },
                )

        if is_down_streak and not ctx.has_position():
            # After a down streak, we expect continuation down. If it fails, we fade (long).
            # Check the next `continuation_period` bars.
            failed_continuation = True
            for i in range(1, self.continuation_period + 1):
                if closes[-i] < closes[-(i+1)]: # If price is still going down
                    failed_continuation = False
                    break

            if failed_continuation:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "failed_continuation_after_down_streak",
                        "streak_period": self.streak_period,
                        "down_streak_count": down_streak_count,
                        "continuation_period": self.continuation_period,
                        "current_price": ctx.bar.close,
                        "regime": ctx.regime,
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close
        self.last_trade_bar = ctx.bar_index
        ctx.log(f"Position closed. Cooldown initiated until bar {self.last_trade_bar + self.cooldown_bars}")