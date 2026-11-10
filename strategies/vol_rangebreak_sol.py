from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRangeBreak(Strategy):
    METADATA = {
        "name": "VolatilityRangeBreak",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # ~3 hours
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.range_period = 20
        self.breakout_threshold_factor = 1.5 # How many ATRs above/below the range
        self.atr_period = 14
        self.cooldown_bars = 12 # Wait 12 bars (12 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period + self.atr_period + 2)
        highs = ctx.highs(self.range_period + self.atr_period + 2)
        lows = ctx.lows(self.range_period + self.atr_period + 2)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        # Define the range using the last 'range_period' bars excluding the current one
        # Look back one more bar to identify the breakout and then the reversal
        range_high = max(highs[-(self.range_period + 1):-1])
        range_low = min(lows[-(self.range_period + 1):-1])
        range_mid = (range_high + range_low) / 2.0

        # Breakout level
        upper_breakout_level = range_high + self.breakout_threshold_factor * current_atr
        lower_breakout_level = range_low - self.breakout_threshold_factor * current_atr

        # Check for failed bullish breakout (price went above, then closed back in/below)
        # Bar N-1 attempted a breakout above the range
        # Bar N (current bar) closes back inside or below the range
        if (highs[-2] > upper_breakout_level and # Previous bar went above breakout level
            closes[-1] < range_mid and # Current bar closed below the midpoint of the range
            ctx.bar.close < closes[-1]): # And current bar is closing lower than previous close
            
            if not ctx.has_position():
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "failed_bullish_range_breakout_reversal",
                        "range_high": range_high,
                        "range_low": range_low,
                        "breakout_level": upper_breakout_level,
                        "prev_high": highs[-2],
                        "current_close": ctx.bar.close,
                        "atr": current_atr,
                    }
                )

        # Check for failed bearish breakout (price went below, then closed back in/above)
        # Bar N-1 attempted a breakout below the range
        # Bar N (current bar) closes back inside or above the range
        if (lows[-2] < lower_breakout_level and # Previous bar went below breakout level
            closes[-1] > range_mid and # Current bar closed above the midpoint of the range
            ctx.bar.close > closes[-1]): # And current bar is closing higher than previous close

            if not ctx.has_position():
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "failed_bearish_range_breakout_reversal",
                        "range_high": range_high,
                        "range_low": range_low,
                        "breakout_level": lower_breakout_level,
                        "prev_low": lows[-2],
                        "current_close": ctx.bar.close,
                        "atr": current_atr,
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy on close.
        # Cooldown is handled in on_bar.
        pass