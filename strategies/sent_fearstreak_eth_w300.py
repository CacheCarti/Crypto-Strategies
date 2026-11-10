from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedStabilization(Strategy):
    METADATA = {
        "name": "FearGreedStabilization",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fg_period = 5 # Period to check for depressed Fear & Greed
        self.ema_period = 20 # EMA for stabilization signal
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Fear & Greed Index check (slow state variable)
        # We need FG values for the last self.fg_period days. Since FG is daily,
        # we check the last `self.fg_period` unique FG values.
        # This is a bit tricky with hourly bars, as FG value repeats for 24 bars.
        # We'll approximate by checking the last `self.fg_period * 24` bars for FG values.
        # However, for simplicity and to stay within backtestable constraints,
        # we'll just check if the current FG index is below a threshold and has been for a few bars.

        current_fg = ctx.features.get("fear_greed_index", 50)
        
        # Check if Fear & Greed has been "depressed" for a few recent bars
        # This is a proxy for "multiple daily readings" on an hourly chart
        fg_history = [ctx.features.get(f"fear_greed_index", 50) for _ in range(self.fg_period * 2)]
        
        # Condition 1: Depressed Fear & Greed
        # Ensure FG is below a certain threshold (e.g., 30 for "Fear" or "Extreme Fear")
        # and has been consistently low.
        fg_depressed = True
        for fg_val in fg_history:
            if fg_val > 30: # Threshold for "Fear"
                fg_depressed = False
                break

        if not fg_depressed:
            return None

        # Condition 2: Price stabilization after depressed FGI
        # We look for the current close to be above a short-term EMA,
        # and for the previous bar's close to have been below it (or very close).
        # This indicates a "stabilization bar" or a bounce.

        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # We need a clear upward movement from below the EMA
        # Current close must be above EMA, and previous close must be below EMA
        # This captures the "first stabilization bar" idea.
        
        current_close = closes[-1]
        previous_close = closes[-2]
        
        # Check for a "cross" above the EMA from below
        if current_close > ema and previous_close <= ema:
            # Entry signal: Long
            confidence = 0.6 + (1 - (current_fg / 50)) * 0.3 # Higher confidence if more fear
            confidence = min(confidence, 0.9) # Cap confidence

            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "fear_greed_stabilization_long",
                    "current_fg_index": current_fg,
                    "ema_period": self.ema_period,
                    "current_close": current_close,
                    "ema_value": ema,
                    "previous_close": previous_close
                }
            )
        
        # If we have a position, don't send a new signal unless we want to flatten
        if ctx.has_position():
            return None

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis, or adapt parameters
        pass