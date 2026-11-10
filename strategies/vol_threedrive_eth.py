from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityThreeDrive(Strategy):
    METADATA = {
        "name": "VolatilityThreeDriveETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60,
        "required_features": [], # No external features used
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars - 1
        self.momentum_period = 10 # For checking momentum strength
        self.drive_threshold_factor = 0.005 # 0.5% move for a drive
        self.weakness_factor = 0.5 # Momentum should drop by 50%
        self.max_drive_bars = 6 # Max bars for a single drive to complete

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None # Still in cooldown

        current_price = ctx.bar.close

        # Check for 3-drive pattern exhaustion
        # A "drive" is a significant directional move followed by a pause/retrace.
        # We look for three consecutive drives in the same direction, with the third showing weakness.

        # Look for bearish 3-drive (SELL signal)
        # Drive 1: Strong move down
        # Drive 2: Another strong move down, potentially less momentum
        # Drive 3: Final move down, significantly weaker momentum, fails to make a strong new low
        # Then price closes above a recent high or reverses.

        # Simplified approach: look for three consecutive bearish candles (or small groups)
        # followed by a bullish candle, where the momentum of the third "push" is weak.
        # This is a pattern of exhaustion after successive attempts to push price lower.

        # Check for 3 consecutive bearish "pushes" followed by a bullish reversal attempt
        # A "push" could be defined by a certain price drop over a few bars.
        
        # We need at least 3*max_drive_bars + a few more bars for this pattern
        if len(closes) < 3 * self.max_drive_bars + 5:
            return None

        # Helper to find a "drive" (a directional move)
        def find_drive(start_idx, direction, max_bars):
            if direction == "down":
                target_price = closes[start_idx] * (1 - self.drive_threshold_factor)
                for i in range(start_idx + 1, min(len(closes), start_idx + 1 + max_bars)):
                    if closes[i] < target_price:
                        # Calculate momentum for this drive
                        momentum = (closes[start_idx] - closes[i]) / closes[start_idx]
                        return i, momentum # End index of drive and its momentum
            elif direction == "up":
                target_price = closes[start_idx] * (1 + self.drive_threshold_factor)
                for i in range(start_idx + 1, min(len(closes), start_idx + 1 + max_bars)):
                    if closes[i] > target_price:
                        momentum = (closes[i] - closes[start_idx]) / closes[start_idx]
                        return i, momentum
            return None, 0.0

        # Look for short setup (three drives down, then failure)
        drive3_end_idx = len(closes) - 1
        
        # Check if the current bar is a reversal bar (bullish close after bearish attempts)
        is_bullish_reversal = (ctx.bar.close > ctx.bar.open) and (ctx.bar.close > closes[-2])

        if is_bullish_reversal:
            # Attempt to find the third drive down (ending at drive3_end_idx - 1)
            end_d3, mom_d3 = find_drive(drive3_end_idx - self.max_drive_bars -1, "down", self.max_drive_bars)
            if end_d3 is None: return None
            
            # Attempt to find the second drive down
            end_d2, mom_d2 = find_drive(end_d3 - self.max_drive_bars -1, "down", self.max_drive_bars)
            if end_d2 is None: return None

            # Attempt to find the first drive down
            end_d1, mom_d1 = find_drive(end_d2 - self.max_drive_bars -1, "down", self.max_drive_bars)
            if end_d1 is None: return None

            # Conditions for a bearish 3-drive exhaustion leading to a long:
            # 1. All three drives are substantial (mom_d > 0.005)
            # 2. Momentum weakens significantly in the third drive compared to the second
            # 3. The current bar is a bullish reversal (checked above)
            
            if mom_d1 > self.drive_threshold_factor and \
               mom_d2 > self.drive_threshold_factor and \
               mom_d3 > self.drive_threshold_factor and \
               mom_d3 < mom_d2 * self.weakness_factor and \
               closes[end_d3] < closes[end_d2] < closes[end_d1]: # Ensure lower lows
                
                # Check for regime filter: Avoid trading in crisis or extreme volatility
                if ctx.market.get("regime", "NORMAL") in ["CRISIS", "HIGH_VOL"]:
                    return None
                
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "three_drive_bearish_exhaustion_long",
                        "momentum_drive1": round(mom_d1, 4),
                        "momentum_drive2": round(mom_d2, 4),
                        "momentum_drive3": round(mom_d3, 4),
                        "reversal_price": current_price,
                        "regime": ctx.market.get("regime", "NORMAL")
                    }
                )

        # Look for long setup (three drives up, then failure)
        # Check if the current bar is a reversal bar (bearish close after bullish attempts)
        is_bearish_reversal = (ctx.bar.close < ctx.bar.open) and (ctx.bar.close < closes[-2])

        if is_bearish_reversal:
            # Attempt to find the third drive up (ending at drive3_end_idx - 1)
            end_d3, mom_d3 = find_drive(drive3_end_idx - self.max_drive_bars -1, "up", self.max_drive_bars)
            if end_d3 is None: return None
            
            # Attempt to find the second drive up
            end_d2, mom_d2 = find_drive(end_d3 - self.max_drive_bars -1, "up", self.max_drive_bars)
            if end_d2 is None: return None

            # Attempt to find the first drive up
            end_d1, mom_d1 = find_drive(end_d2 - self.max_drive_bars -1, "up", self.max_drive_bars)
            if end_d1 is None: return None

            # Conditions for a bullish 3-drive exhaustion leading to a short:
            # 1. All three drives are substantial (mom_d > 0.005)
            # 2. Momentum weakens significantly in the third drive compared to the second
            # 3. The current bar is a bearish reversal (checked above)

            if mom_d1 > self.drive_threshold_factor and \
               mom_d2 > self.drive_threshold_factor and \
               mom_d3 > self.drive_threshold_factor and \
               mom_d3 < mom_d2 * self.weakness_factor and \
               closes[end_d3] > closes[end_d2] > closes[end_d1]: # Ensure higher highs
                
                # Check for regime filter: Avoid trading in crisis or extreme volatility
                if ctx.market.get("regime", "NORMAL") in ["CRISIS", "HIGH_VOL"]:
                    return None

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "three_drive_bullish_exhaustion_short",
                        "momentum_drive1": round(mom_d1, 4),
                        "momentum_drive2": round(mom_d2, 4),
                        "momentum_drive3": round(mom_d3, 4),
                        "reversal_price": current_price,
                        "regime": ctx.market.get("regime", "NORMAL")
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close, or adapt based on performance
        # For now, just log that a position closed
        pass