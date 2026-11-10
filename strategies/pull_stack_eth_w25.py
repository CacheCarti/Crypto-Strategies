from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StructuralPullback(Strategy):
    METADATA = {
        "name": "Structural Pullback",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Roughly 4 hours
        "warmup_bars": 50, # Need enough bars for swing high/low detection and EMA
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.swing_lookback = 10 # Lookback for swing highs/lows
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _is_higher_high(self, highs, current_idx, lookback):
        if current_idx < lookback:
            return False
        return highs[current_idx] > max(highs[current_idx - lookback:current_idx])

    def _is_higher_low(self, lows, current_idx, lookback):
        if current_idx < lookback:
            return False
        return lows[current_idx] > max(lows[current_idx - lookback:current_idx])

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close

        # Calculate EMA
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar_index + self.cooldown_bars:
            return None

        # --- Entry Logic ---
        if not ctx.has_position():
            # Check for structural uptrend (higher highs and higher lows)
            # This is a simplified check, focusing on recent structure
            if len(highs) >= self.swing_lookback + 2 and len(lows) >= self.swing_lookback + 2:
                # Look for a sequence: recent high > previous high AND recent low > previous low
                # This is a basic form of trend detection.
                recent_high = max(highs[-self.swing_lookback:])
                previous_high = max(highs[-self.swing_lookback - 1 : -1]) # Max of period before recent
                
                recent_low = min(lows[-self.swing_lookback:])
                previous_low = min(lows[-self.swing_lookback - 1 : -1]) # Min of period before recent

                # Simplified check for higher high and higher low.
                # We need to ensure we're not just picking up noise.
                # A more robust check would track actual swing points.
                is_uptrend = recent_high > previous_high and recent_low > previous_low and previous_high > highs[-self.swing_lookback - 2]
                
                # Condition 1: Price is above EMA (uptrend confirmation)
                price_above_ema = current_price > ema

                # Condition 2: Price has pulled back towards EMA but is now turning up
                # This is a heuristic: current close is higher than previous close,
                # and current low touched or went below EMA
                pulled_back = closes[-2] > ema and current_price > closes[-2] and lows[-1] <= ema

                if is_uptrend and price_above_ema and pulled_back:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "structural_pullback_long",
                            "current_price": current_price,
                            "ema": ema,
                            "recent_high": recent_high,
                            "previous_high": previous_high,
                            "recent_low": recent_low,
                            "previous_low": previous_low,
                            "regime": ctx.regime,
                        }
                    )

        # --- Exit Logic (if holding a position) ---
        if ctx.has_position() and ctx.position_direction() == "long":
            # Exit if structure breaks: current close below a recent low (defined by EMA)
            # Or if price crosses below EMA after a pullback signal
            
            # Simple structure break: close below EMA
            if current_price < ema:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "structure_break_ema_cross_down",
                        "current_price": current_price,
                        "ema": ema,
                        "regime": ctx.regime,
                    }
                )

            # Exit on prior high (take profit) - this is less precise than declared TP
            # but can act as an additional exit condition for specific structural targets.
            # For simplicity, we mostly rely on declared TP, but can add a check like:
            # if current_price >= self.entry_high_target: # Needs to be set at entry
            #    return ctx.signal(...)
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not strictly needed for this strategy, but good practice for adaptive logic.
        pass