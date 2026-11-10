from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityThreeDrive(Strategy):
    METADATA = {
        "name": "Volatility Three Drive",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour holds
        "warmup_bars": 60, # Need enough bars for ATR and momentum
        "required_features": [], # No external features needed for this exploration
    }

    def initialize(self, ctx: BarContext) -> None:
        self.momentum_period = 10
        self.atr_period = 20
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Thresholds for directional push and momentum weakening
        self.push_threshold = 0.5 # Percentage of ATR for a significant push
        self.momentum_weakening_factor = 0.8 # Momentum of 3rd push should be < factor * momentum of 2nd push
        self.max_candle_size_ratio = 1.5 # Max ratio of current candle to average candle size
        self.min_push_candles = 3 # Minimum candles for a directional push to be considered

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _momentum(self, closes, period):
        if len(closes) < period:
            return None
        return closes[-1] - closes[-period]

    def _is_directional_push(self, closes, highs, lows, start_idx, end_idx, direction, avg_atr):
        if end_idx - start_idx < self.min_push_candles:
            return False, 0.0

        push_range = closes[end_idx] - closes[start_idx]
        push_momentum = abs(push_range)

        if push_momentum < self.push_threshold * avg_atr:
            return False, 0.0

        if direction == "long" and push_range > 0:
            return True, push_momentum
        elif direction == "short" and push_range < 0:
            return True, push_momentum
        return False, 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filter: only trade in 'normal' or 'volatile' regimes
        if ctx.market.get("regime", "NORMAL") not in ["NORMAL", "HIGH_VOL"]:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None or current_atr == 0:
            return None

        # Check for clean event boundary: current candle not excessively large
        avg_candle_size = sum([h - l for h, l in zip(highs[-self.atr_period:], lows[-self.atr_period:])]) / self.atr_period
        if (ctx.bar.high - ctx.bar.low) > self.max_candle_size_ratio * avg_candle_size:
            return None # Skip if current candle is too chaotic/large

        # Look for three directional pushes
        # Iterate backwards to find recent pushes
        pushes = [] # Stores (end_bar_index, direction, momentum_value)
        direction = None # "long" or "short"

        for i in range(len(closes) - 2, self.atr_period - 1, -1): # Start from second to last bar
            current_direction = "long" if closes[i+1] > closes[i] else "short" if closes[i+1] < closes[i] else None

            if current_direction is None:
                continue

            if not pushes: # First push
                pushes.append((i+1, current_direction, abs(closes[i+1] - closes[i])))
                direction = current_direction
            elif current_direction == direction: # Continue current push
                pushes[-1] = (i+1, current_direction, abs(closes[i+1] - closes[pushes[-1][0]]))
            else: # New push in opposite direction
                pushes.append((i+1, current_direction, abs(closes[i+1] - closes[i])))
                direction = current_direction

            if len(pushes) >= 3:
                break

        if len(pushes) < 3:
            return None

        # Check for three successive directional pushes in the SAME direction
        # The logic above might pick alternating pushes, we need to filter for consecutive same-direction pushes
        filtered_pushes = []
        for i in range(len(pushes) - 1, -1, -1): # Iterate from most recent to oldest
            if not filtered_pushes:
                filtered_pushes.append(pushes[i])
            elif pushes[i][1] == filtered_pushes[-1][1]:
                # Merge consecutive pushes in the same direction
                filtered_pushes[-1] = (pushes[i][0], pushes[i][1], filtered_pushes[-1][2] + pushes[i][2])
            else:
                filtered_pushes.append(pushes[i])

        if len(filtered_pushes) < 3:
            return None

        # We need 3 distinct pushes, not merged ones. Let's re-think the push detection.
        # Simple approach: Find 3 consecutive candles with same direction, then check if the next one fails
        # This is more aligned with "third successive directional push"
        
        # Look for 3 consecutive up/down candles
        # We need at least 4 bars for 3 pushes + 1 failure bar
        if len(closes) < 4:
            return None
        
        # Check for three successive UP pushes (potential short entry)
        if closes[-1] > closes[-2] and closes[-2] > closes[-3] and closes[-3] > closes[-4]:
            # This is 3 pushes up. Now check if the 4th candle (current bar) fails to continue or reverses
            # A "failed" next push could mean a lower close than open, or a close below the previous candle's close
            if ctx.bar.close < ctx.bar.open or ctx.bar.close < closes[-1]:
                # Momentum check: is the current "failure" candle relatively small compared to previous pushes?
                # Or, is the momentum of the 3rd push weaker than the 2nd? (Hard to define with simple candles)
                
                # Let's simplify: 3 green candles, then a red one (or small green)
                # And the price is near the top of its recent range (high RSI, but we don't use RSI here)
                
                # Instead of momentum, let's use the size of the candles
                push1_size = closes[-3] - closes[-4]
                push2_size = closes[-2] - closes[-3]
                push3_size = closes[-1] - closes[-2]
                
                if push3_size < push2_size * self.momentum_weakening_factor and \
                   ctx.bar.close < closes[-1] - 0.5 * current_atr: # Significant reversal or weakening
                    
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "three_drive_up_failure",
                            "push1_size": push1_size,
                            "push2_size": push2_size,
                            "push3_size": push3_size,
                            "current_bar_change": ctx.bar.close - ctx.bar.open,
                            "current_atr": current_atr,
                            "regime": ctx.market.get("regime", "NORMAL")
                        }
                    )

        # Check for three successive DOWN pushes (potential long entry)
        if closes[-1] < closes[-2] and closes[-2] < closes[-3] and closes[-3] < closes[-4]:
            # This is 3 pushes down. Now check if the 4th candle (current bar) fails to continue or reverses
            if ctx.bar.close > ctx.bar.open or ctx.bar.close > closes[-1]:
                push1_size = closes[-4] - closes[-3] # absolute value
                push2_size = closes[-3] - closes[-2]
                push3_size = closes[-2] - closes[-1]
                
                if push3_size < push2_size * self.momentum_weakening_factor and \
                   ctx.bar.close > closes[-1] + 0.5 * current_atr: # Significant reversal or strengthening
                    
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "three_drive_down_failure",
                            "push1_size": push1_size,
                            "push2_size": push2_size,
                            "push3_size": push3_size,
                            "current_bar_change": ctx.bar.close - ctx.bar.open,
                            "current_atr": current_atr,
                            "regime": ctx.market.get("regime", "NORMAL")
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close to ensure next trade isn't too soon
        self.last_trade_bar = ctx.bar_index
        pass