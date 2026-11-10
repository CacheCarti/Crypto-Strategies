from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ThreeDrivesExhaustion(Strategy):
    METADATA = {
        "name": "Three Drives Exhaustion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Intended hold for a few hours
        "warmup_bars": 50,  # Need enough bars for EMA and momentum calculations
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.drive_period = 10  # Bars to define a "drive"
        self.momentum_period = 5 # Bars to calculate momentum
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _momentum_change(self, closes, period):
        if len(closes) < period + 1:
            return None
        # Simple momentum: current price vs price 'period' bars ago
        # We need relative change to detect weakening
        current_price = closes[-1]
        past_price = closes[-period-1]
        if past_price == 0:
            return 0.0
        return (current_price - past_price) / past_price

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.drive_period * 3 + self.momentum_period + 2)
        highs = ctx.highs(self.drive_period * 3 + self.momentum_period + 2)
        lows = ctx.lows(self.drive_period * 3 + self.momentum_period + 2)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        if ctx.has_position():
            # If in a position, we let the engine handle SL/TP or hold
            # but we can look for early exit based on the midpoint of the last drive
            if ctx.position_direction() == "long":
                # Check for midpoint of the third drive as a potential exit
                # This requires storing drive details in metadata at entry
                if ctx.position and ctx.position.get("metadata", {}).get("midpoint_third_drive"):
                    midpoint = ctx.position["metadata"]["midpoint_third_drive"]
                    if ctx.bar.close < midpoint:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_exit_midpoint_third_drive", "current_price": ctx.bar.close, "midpoint": midpoint})
            elif ctx.position_direction() == "short":
                if ctx.position and ctx.position.get("metadata", {}).get("midpoint_third_drive"):
                    midpoint = ctx.position["metadata"]["midpoint_third_drive"]
                    if ctx.bar.close > midpoint:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_exit_midpoint_third_drive", "current_price": ctx.bar.close, "midpoint": midpoint})
            return None


        # Check for 3 drives up (potential short)
        # Drive 1: closes[-3*drive_period-1 : -2*drive_period-1]
        # Drive 2: closes[-2*drive_period-1 : -drive_period-1]
        # Drive 3: closes[-drive_period-1 : -1]

        # Calculate drives and momentum for potential short
        drive1_up_start_idx = len(closes) - (3 * self.drive_period) - 1
        drive2_up_start_idx = len(closes) - (2 * self.drive_period) - 1
        drive3_up_start_idx = len(closes) - self.drive_period - 1

        if drive1_up_start_idx >= 0 and closes[drive1_up_start_idx] < closes[drive2_up_start_idx] and \
           closes[drive2_up_start_idx] < closes[drive3_up_start_idx] and \
           closes[drive3_up_start_idx] < closes[-1]:

            # Check if each drive is an actual push higher
            is_drive1_up = closes[drive2_up_start_idx] > closes[drive1_up_start_idx]
            is_drive2_up = closes[drive3_up_start_idx] > closes[drive2_up_start_idx]
            is_drive3_up = closes[-1] > closes[drive3_up_start_idx]

            if is_drive1_up and is_drive2_up and is_drive3_up:
                # Calculate momentum for each drive
                mom1 = self._momentum_change(closes[:drive2_up_start_idx+1], self.momentum_period)
                mom2 = self._momentum_change(closes[:drive3_up_start_idx+1], self.momentum_period)
                mom3 = self._momentum_change(closes, self.momentum_period)

                if all(m is not None for m in [mom1, mom2, mom3]):
                    # Check for weakening momentum (mom1 > mom2 > mom3) AND all positive
                    if mom1 > 0 and mom2 > 0 and mom3 > 0 and mom1 > mom2 and mom2 > mom3:
                        # Find the highest point of the third drive for stop loss
                        third_drive_highs = highs[drive3_up_start_idx:]
                        if third_drive_highs:
                            highest_in_third_drive = max(third_drive_highs)
                            midpoint_third_drive = (closes[drive3_up_start_idx] + closes[-1]) / 2

                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.75,
                                stop_loss_bps=self.METADATA["declared_sl_bps"], # Use declared SL
                                take_profit_bps=self.METADATA["declared_tp_bps"], # Use declared TP
                                metadata={
                                    "reason": "three_drives_up_exhaustion",
                                    "momentum_drive1": mom1,
                                    "momentum_drive2": mom2,
                                    "momentum_drive3": mom3,
                                    "highest_in_third_drive": highest_in_third_drive,
                                    "midpoint_third_drive": midpoint_third_drive,
                                    "current_price": ctx.bar.close
                                }
                            )

        # Calculate drives and momentum for potential long
        drive1_down_start_idx = len(closes) - (3 * self.drive_period) - 1
        drive2_down_start_idx = len(closes) - (2 * self.drive_period) - 1
        drive3_down_start_idx = len(closes) - self.drive_period - 1

        if drive1_down_start_idx >= 0 and closes[drive1_down_start_idx] > closes[drive2_down_start_idx] and \
           closes[drive2_down_start_idx] > closes[drive3_down_start_idx] and \
           closes[drive3_down_start_idx] > closes[-1]:

            # Check if each drive is an actual push lower
            is_drive1_down = closes[drive2_down_start_idx] < closes[drive1_down_start_idx]
            is_drive2_down = closes[drive3_down_start_idx] < closes[drive2_down_start_idx]
            is_drive3_down = closes[-1] < closes[drive3_down_start_idx]

            if is_drive1_down and is_drive2_down and is_drive3_down:
                # Calculate momentum for each drive
                mom1 = self._momentum_change(closes[:drive2_down_start_idx+1], self.momentum_period)
                mom2 = self._momentum_change(closes[:drive3_down_start_idx+1], self.momentum_period)
                mom3 = self._momentum_change(closes, self.momentum_period)

                if all(m is not None for m in [mom1, mom2, mom3]):
                    # Check for weakening momentum (mom1 < mom2 < mom3) AND all negative
                    if mom1 < 0 and mom2 < 0 and mom3 < 0 and mom1 < mom2 and mom2 < mom3:
                        # Find the lowest point of the third drive for stop loss
                        third_drive_lows = lows[drive3_down_start_idx:]
                        if third_drive_lows:
                            lowest_in_third_drive = min(third_drive_lows)
                            midpoint_third_drive = (closes[drive3_down_start_idx] + closes[-1]) / 2

                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.75,
                                stop_loss_bps=self.METADATA["declared_sl_bps"], # Use declared SL
                                take_profit_bps=self.METADATA["declared_tp_bps"], # Use declared TP
                                metadata={
                                    "reason": "three_drives_down_exhaustion",
                                    "momentum_drive1": mom1,
                                    "momentum_drive2": mom2,
                                    "momentum_drive3": mom3,
                                    "lowest_in_third_drive": lowest_in_third_drive,
                                    "midpoint_third_drive": midpoint_third_drive,
                                    "current_price": ctx.bar.close
                                }
                            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Implement adaptive logic or logging here if needed
        pass