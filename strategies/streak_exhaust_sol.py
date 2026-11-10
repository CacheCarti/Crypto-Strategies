from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakExhaustionSOL(Strategy):
    METADATA = {
        "name": "StreakExhaustionSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, aim for 1-4 bars hold
        "warmup_bars": 50,
        "required_features": [], # No specific features needed beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_period = 7 # Number of consecutive bars to define a "streak"
        self.fail_period = 1 # How many bars to check for failure after streak
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

        # For mean reversion target: short EMA
        self.ema_period = 10

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open

        # Calculate EMA for mean reversion target
        ema_val = self._ema(closes, self.ema_period)
        if ema_val is None:
            return None

        # Determine streak direction and length
        streak_direction = None
        streak_length = 0
        for i in range(len(closes) - 2, -1, -1): # Iterate backwards from second to last bar
            if closes[i] > opens[i]: # Green bar
                if streak_direction == "down" or streak_direction is None:
                    streak_direction = "up"
                    streak_length = 1
                elif streak_direction == "up":
                    streak_length += 1
            elif closes[i] < opens[i]: # Red bar
                if streak_direction == "up" or streak_direction is None:
                    streak_direction = "down"
                    streak_length = 1
                elif streak_direction == "down":
                    streak_length += 1
            else: # Doji or flat bar, break streak
                break
        
        # Check for streak exhaustion and failed continuation
        if streak_length >= self.streak_period:
            # Check the current bar (most recent) for failed continuation
            if streak_direction == "up": # Long streak of green bars
                # Look for a red bar (failed continuation)
                if current_close < current_open:
                    # Current bar is red, fading the long streak
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "long_streak_exhaustion_fade",
                                "streak_direction": "up",
                                "streak_length": streak_length,
                                "current_close": current_close,
                                "current_open": current_open,
                                "ema_target": ema_val
                            }
                        )
            elif streak_direction == "down": # Long streak of red bars
                # Look for a green bar (failed continuation)
                if current_close > current_open:
                    # Current bar is green, fading the short streak
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "short_streak_exhaustion_fade",
                                "streak_direction": "down",
                                "streak_length": streak_length,
                                "current_close": current_close,
                                "current_open": current_open,
                                "ema_target": ema_val
                            }
                        )
        
        # Exit logic
        if ctx.has_position():
            direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]

            # Mean reversion target exit
            if direction == "long" and current_close > ema_val:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "mean_reversion_target_hit",
                        "exit_type": "tp_ema",
                        "current_close": current_close,
                        "ema_target": ema_val,
                        "entry_price": entry_price
                    }
                )
            elif direction == "short" and current_close < ema_val:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "mean_reversion_target_hit",
                        "exit_type": "tp_ema",
                        "current_close": current_close,
                        "ema_target": ema_val,
                        "entry_price": entry_price
                    }
                )
            
            # Time stop exit (if held too long and not hitting target)
            bars_held = ctx.bar_index - self.last_trade_bar # Approximate bars held
            if bars_held > self.METADATA["declared_hold_seconds"] // 3600 + 2: # +2 for buffer
                 return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_stop_exit",
                        "exit_type": "time_stop",
                        "bars_held": bars_held,
                        "current_close": current_close,
                        "entry_price": entry_price
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar for cooldown, if not already set by an entry signal
        self.last_trade_bar = ctx.bar_index