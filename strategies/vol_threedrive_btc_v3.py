from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolThreeDriveBTC(Strategy):
    METADATA = {
        "name": "Vol Three Drive BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for 4-hour holds
        "warmup_bars": 60,  # Need enough bars for 3 drives + momentum
        "required_features": [], # No external features needed for this pure price action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.drive_period = 10 # Bars to define a "drive"
        self.momentum_period = 5 # Bars to check momentum
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _get_momentum(self, closes, period):
        if len(closes) < period + 1:
            return None
        return closes[-1] - closes[-period-1]

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check for sufficient recent volatility (using standard deviation of closes)
        volatility_window = 20
        if len(closes) < volatility_window:
            return None
        recent_stddev = self._stddev(closes, volatility_window)
        if recent_stddev is None or recent_stddev < current_price * 0.001: # Require at least 0.1% volatility
            return None

        # --- Long Setup: Three lower drives, momentum weakening, next push fails ---
        # A "drive" is defined by a significant low followed by a bounce
        # We look for 3 consecutive drives where each low is lower than the previous
        # and the bounce is weaker.

        long_signal_found = False
        if len(lows) >= self.drive_period * 3 + self.momentum_period:
            # Check for three successive lower lows, each followed by a bounce
            # And weakening momentum in the bounces

            # Drive 1: Find a low, then a bounce
            low1_idx = -1
            for i in range(self.drive_period * 3, self.drive_period * 2, -1):
                if lows[i] == min(lows[i-self.drive_period:i+1]):
                    low1_idx = i
                    break
            
            if low1_idx != -1 and low1_idx < len(lows) - 1:
                bounce1_start = closes[low1_idx]
                bounce1_end = closes[min(low1_idx + self.momentum_period, len(closes) - 1)]
                momentum1 = bounce1_end - bounce1_start
                
                # Drive 2: Find a lower low after drive 1, then a bounce
                low2_idx = -1
                for i in range(low1_idx + 1, self.drive_period * 2 + 1):
                    if lows[i] == min(lows[i-self.drive_period:i+1]) and lows[i] < lows[low1_idx]:
                        low2_idx = i
                        break

                if low2_idx != -1 and low2_idx < len(lows) - 1:
                    bounce2_start = closes[low2_idx]
                    bounce2_end = closes[min(low2_idx + self.momentum_period, len(closes) - 1)]
                    momentum2 = bounce2_end - bounce2_start

                    # Drive 3: Find an even lower low after drive 2, then a bounce
                    low3_idx = -1
                    for i in range(low2_idx + 1, len(lows)):
                        if lows[i] == min(lows[i-self.drive_period:i+1]) and lows[i] < lows[low2_idx]:
                            low3_idx = i
                            break

                    if low3_idx != -1 and low3_idx == len(lows) - 1: # Current bar is the low of the 3rd drive
                        # Check for weakening momentum: momentum1 > momentum2 > momentum3 (approx)
                        # We need to make sure we have enough bars for the third bounce to form
                        # For simplicity, we'll check if the current price is failing to bounce strongly
                        
                        # A simple check for failed bounce: current close is near the low or has not significantly rebounded
                        if current_price <= lows[low3_idx] * 1.005: # Price is not bouncing strongly
                            # Ensure previous momentum was stronger
                            # This is a simplification. A more robust check would involve comparing actual bounce strength.
                            # For now, we assume the pattern of lower lows with potentially weaker bounces implies reversal.
                            
                            # Additional check: current bar is a hammer or similar reversal candle
                            # Or a close above the open after a recent low
                            if ctx.bar.close > ctx.bar.open and (ctx.bar.close - ctx.bar.low) / (ctx.bar.high - ctx.bar.low) > 0.6: # Closing in upper part of range
                                long_signal_found = True
                                self.last_trade_bar = ctx.bar_index
                                return ctx.signal(
                                    "long",
                                    confidence=0.7,
                                    metadata={
                                        "reason": "vol_three_drive_long_reversal",
                                        "low1": lows[low1_idx],
                                        "low2": lows[low2_idx],
                                        "low3": lows[low3_idx],
                                        "current_price": current_price,
                                        "volatility": recent_stddev
                                    }
                                )

        # --- Short Setup: Three higher drives, momentum weakening, next push fails ---
        # Similar logic for short, looking for higher highs and weakening momentum

        short_signal_found = False
        if len(highs) >= self.drive_period * 3 + self.momentum_period:
            # Check for three successive higher highs, each followed by a pull back
            # And weakening momentum in the pushes

            # Drive 1: Find a high, then a pullback
            high1_idx = -1
            for i in range(self.drive_period * 3, self.drive_period * 2, -1):
                if highs[i] == max(highs[i-self.drive_period:i+1]):
                    high1_idx = i
                    break
            
            if high1_idx != -1 and high1_idx < len(highs) - 1:
                push1_start = closes[high1_idx]
                push1_end = closes[min(high1_idx + self.momentum_period, len(closes) - 1)]
                momentum1 = push1_end - push1_start # Should be negative for pullback

                # Drive 2: Find a higher high after drive 1, then a pullback
                high2_idx = -1
                for i in range(high1_idx + 1, self.drive_period * 2 + 1):
                    if highs[i] == max(highs[i-self.drive_period:i+1]) and highs[i] > highs[high1_idx]:
                        high2_idx = i
                        break

                if high2_idx != -1 and high2_idx < len(highs) - 1:
                    push2_start = closes[high2_idx]
                    push2_end = closes[min(high2_idx + self.momentum_period, len(closes) - 1)]
                    momentum2 = push2_end - push2_start

                    # Drive 3: Find an even higher high after drive 2, current bar is high
                    high3_idx = -1
                    for i in range(high2_idx + 1, len(highs)):
                        if highs[i] == max(highs[i-self.drive_period:i+1]) and highs[i] > highs[high2_idx]:
                            high3_idx = i
                            break

                    if high3_idx != -1 and high3_idx == len(highs) - 1: # Current bar is the high of the 3rd drive
                        # Check for failed pullback: current close is near the high or has not significantly pulled back
                        if current_price >= highs[high3_idx] * 0.995: # Price is not pulling back strongly
                            # Additional check: current bar is a shooting star or similar reversal candle
                            # Or a close below the open after a recent high
                            if ctx.bar.close < ctx.bar.open and (ctx.bar.high - ctx.bar.close) / (ctx.bar.high - ctx.bar.low) > 0.6: # Closing in lower part of range
                                short_signal_found = True
                                self.last_trade_bar = ctx.bar_index
                                return ctx.signal(
                                    "short",
                                    confidence=0.7,
                                    metadata={
                                        "reason": "vol_three_drive_short_reversal",
                                        "high1": highs[high1_idx],
                                        "high2": highs[high2_idx],
                                        "high3": highs[high3_idx],
                                        "current_price": current_price,
                                        "volatility": recent_stddev
                                    }
                                )

        # If we have a position, and we haven't found a new signal to flip, we hold.
        # Otherwise, if we found a signal, it would have been returned.
        # If no signal and no position, do nothing.
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar on close to allow for new entries after cooldown
        self.last_trade_bar = ctx.bar_index
        # You could implement adaptive sizing here based on position['return_bps']
        pass