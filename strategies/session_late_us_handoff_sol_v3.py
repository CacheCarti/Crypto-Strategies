from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionHandoff(Strategy):
    METADATA = {
        "name": "SolSessionHandoff",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold for one session (1 hour bar)
        "warmup_bars": 30,
        "required_features": [], # No external features needed for this price-action based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 18 # UTC 18:00 (late US session start)
        self.entry_hour_end = 23   # UTC 23:00 (late US session end for entry)
        self.exit_hour = 0         # UTC 0:00 (end of session, beginning of new day)
        self.min_session_bars = 4  # Minimum bars to observe for session direction
        self.lookback_period = 6   # For EMA and price delta
        self.cooldown_bars = 5     # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + self.min_session_bars + 1)
        if len(closes) < self.lookback_period + self.min_session_bars + 1:
            return None # Not enough data for warmup

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: Close position before the next session handoff (UTC 0:00)
        if ctx.has_position() and current_hour == self.exit_hour:
            # Only exit if the position was entered in the previous session
            if ctx.position.get("metadata", {}).get("entry_hour") != self.exit_hour:
                 return ctx.signal("flat", confidence=0.7, metadata={
                     "reason": "session_boundary_exit",
                     "exit_hour": self.exit_hour,
                     "current_price": ctx.bar.close
                 })

        # Do not enter if already in a position
        if ctx.has_position():
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: Only consider entries within the specified late US session window
        if self.entry_hour_start <= current_hour <= self.entry_hour_end:
            # Calculate session directional impulse
            session_closes = ctx.closes(self.min_session_bars + 1) # Current bar + min_session_bars prior
            if len(session_closes) < self.min_session_bars + 1:
                return None

            # Calculate the directional impulse over the observed session bars
            session_start_price = session_closes[0]
            session_end_price = ctx.bar.close
            price_delta = session_end_price - session_start_price

            # Use an EMA for trend confirmation
            current_ema = self._ema(closes, self.lookback_period)
            if current_ema is None:
                return None

            # Long condition: price moving up within the session and above EMA
            if price_delta > 0 and ctx.bar.close > current_ema:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.75, metadata={
                    "reason": "late_us_session_long_impulse",
                    "entry_hour": current_hour,
                    "session_start_price": session_start_price,
                    "current_price": ctx.bar.close,
                    "price_delta": price_delta,
                    "ema": current_ema
                })

            # Short condition: price moving down within the session and below EMA
            elif price_delta < 0 and ctx.bar.close < current_ema:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.75, metadata={
                    "reason": "late_us_session_short_impulse",
                    "entry_hour": current_hour,
                    "session_start_price": session_start_price,
                    "current_price": ctx.bar.close,
                    "price_delta": price_delta,
                    "ema": current_ema
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy, but could be added here.
        pass