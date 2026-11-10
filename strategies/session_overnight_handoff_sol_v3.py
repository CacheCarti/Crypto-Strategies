from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolOvernightHandoff(Strategy):
    METADATA = {
        "name": "SolOvernightHandoff",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for a 6-hour hold
        "warmup_bars": 50,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 0  # UTC 00:00
        self.entry_hour_end = 6    # UTC 06:00
        self.exit_hour = 12        # UTC 12:00 (before US session starts strongly)
        self.lookback_period = 20  # For EMA and price change
        self.cooldown_bars = 6     # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        if len(closes) < self.lookback_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Exit logic: if we have a position and it's time to exit
        if ctx.has_position():
            if current_hour >= self.exit_hour:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_boundary_exit",
                    "exit_hour": self.exit_hour,
                    "current_hour": current_hour,
                    "price": current_close
                })
            # If we are in a position, do not consider new entries
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: only within the overnight window
        if self.entry_hour_start <= current_hour < self.entry_hour_end:
            # Measure directional impulse from the start of the session (approx)
            # Use the change from the open of the session's first bar
            # This is a simplified approach, as we don't know the exact session open
            # We'll use the open of the current bar vs a recent EMA
            
            ema_val = self._ema(closes, self.lookback_period)
            if ema_val is None:
                return None

            # Simple impulse: price above/below EMA
            # And a small positive/negative change from the previous bar to confirm momentum
            prev_close = closes[-2]
            
            # Long condition: price above EMA and showing positive momentum
            if current_close > ema_val and current_close > prev_close:
                # Confirm follow-through with a slight additional move
                if (current_close - ema_val) / ema_val > 0.001: # 0.1% above EMA
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("long", confidence=0.75, metadata={
                        "reason": "overnight_long_impulse_handoff",
                        "entry_hour": current_hour,
                        "close": current_close,
                        "ema": ema_val,
                        "prev_close": prev_close
                    })

            # Short condition: price below EMA and showing negative momentum
            elif current_close < ema_val and current_close < prev_close:
                # Confirm follow-through with a slight additional move
                if (ema_val - current_close) / ema_val > 0.001: # 0.1% below EMA
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("short", confidence=0.75, metadata={
                        "reason": "overnight_short_impulse_handoff",
                        "entry_hour": current_hour,
                        "close": current_close,
                        "ema": ema_val,
                        "prev_close": prev_close
                    })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We update last_trade_bar on entry, so no need to update on close for cooldown.
        # This method could be used for adaptive sizing or logging.
        pass