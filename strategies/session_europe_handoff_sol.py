from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionImpulse(Strategy):
    METADATA = {
        "name": "SolSessionImpulse",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold roughly until session close
        "warmup_bars": 30,
        "required_features": [], # No external features needed for this hypothesis
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7 # UTC
        self.entry_hour_end = 12 # UTC
        self.exit_hour = 12 # UTC
        self.session_lookback_bars = 4 # Lookback for session impulse (4 hours)
        self.min_impulse_pct = 0.005 # 0.5% move in session to consider
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade
        self.last_trade_bar_index = -self.cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.session_lookback_bars + 1)
        opens = ctx.opens(self.session_lookback_bars + 1)
        
        if len(closes) < self.session_lookback_bars + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic
        if ctx.has_position():
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_close_time",
                    "current_hour": current_hour,
                    "entry_bar_index": ctx.position["metadata"].get("entry_bar_index", -1)
                })
            # Structure stop/reversal logic (simple: if price goes against us significantly)
            # This is a basic example; more complex structure stops could be implemented
            entry_price = ctx.position["entry_price"]
            current_price = ctx.bar.close
            direction = ctx.position["direction"]
            
            # Simple structure stop: if price reverses more than half the declared SL
            # This is an early exit to protect against false breakouts
            if direction == "long" and current_price < entry_price * (1 - self.METADATA["declared_sl_bps"] / 2 / 10000):
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "structure_stop_reversal",
                    "current_price": current_price,
                    "entry_price": entry_price,
                    "direction": direction
                })
            elif direction == "short" and current_price > entry_price * (1 + self.METADATA["declared_sl_bps"] / 2 / 10000):
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "structure_stop_reversal",
                    "current_price": current_price,
                    "entry_price": entry_price,
                    "direction": direction
                })
            
            return None # Hold position if no exit condition met

        # Entry logic
        if current_hour < self.entry_hour_start or current_hour >= self.entry_hour_end:
            return None # Only look for entries during the defined session hours

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Calculate session impulse
        session_open = opens[0]
        session_close = closes[-1] # Current close
        
        # Check if we have enough bars for the session impulse calculation
        if len(closes) <= self.session_lookback_bars:
            return None
        
        # We need the open price from self.session_lookback_bars bars ago
        # and the current close price.
        # This assumes the 'session' is defined as the last 'session_lookback_bars'
        # leading up to the current bar.
        
        # Let's refine: The 'session' is the period from entry_hour_start up to current_hour.
        # So we need the price at the start of the session (entry_hour_start)
        # and the current price.
        
        # This strategy uses the price action within the current European morning session
        # to determine its impulse.
        
        # We need to find the open price at the start of the current session on the current day.
        # This requires looking back to the bar that corresponds to self.entry_hour_start.
        
        # A simpler way given current ctx API constraints (no direct access to arbitrary past bar data by time):
        # We will define the impulse as the change over the last `session_lookback_bars`
        # and trigger if this impulse is strong enough AND it's within the entry window.
        
        # Impulse from 'session_lookback_bars' ago to now
        impulse_start_price = closes[-(self.session_lookback_bars + 1)] 
        impulse_end_price = closes[-1]
        
        price_change_pct = (impulse_end_price - impulse_start_price) / impulse_start_price

        signal_direction = None
        reason = ""
        
        if price_change_pct >= self.min_impulse_pct:
            signal_direction = "long"
            reason = "european_session_strong_up_impulse"
        elif price_change_pct <= -self.min_impulse_pct:
            signal_direction = "short"
            reason = "european_session_strong_down_impulse"

        if signal_direction:
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=0.8, # High confidence if impulse is strong
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "price_change_pct": price_change_pct,
                    "impulse_lookback_bars": self.session_lookback_bars,
                    "entry_bar_index": ctx.bar_index
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We store last_trade_bar_index in on_bar for entries.
        # We don't need to update it here for cooldown, as cooldown applies per entry.
        pass