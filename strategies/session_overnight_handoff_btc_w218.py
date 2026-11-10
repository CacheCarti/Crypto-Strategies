from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BTCSessionImpulse(Strategy):
    METADATA = {
        "name": "BTC Session Impulse",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # Aim for 2-4 hour holds
        "warmup_bars": 48,  # Need enough bars for 24-hour lookback for session analysis
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -100
        self.cooldown_bars = 6
        self.session_lookback_hours = 6 # Overnight session 0-6 UTC
        self.entry_hour_start = 6 # Enter after overnight session close
        self.entry_hour_end = 9 # Try to enter before US session really kicks in
        self.exit_hour = 12 # Exit before US afternoon session

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.session_lookback_hours + 1)
        if len(closes) < self.session_lookback_hours + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown period
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Exit logic
        if ctx.has_position():
            if current_hour >= self.exit_hour:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_exit_time",
                    "current_hour": current_hour,
                    "exit_hour_boundary": self.exit_hour
                })
            # Simple trailing stop proxy: if price moves significantly against, exit
            # This is a basic example, a real trailing stop is handled by the engine.
            if ctx.position_direction() == "long" and ctx.bar.close < ctx.position["entry_price"] * (1 - self.METADATA["declared_sl_bps"] / 10000.0 * 0.75):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "preemptive_stop_loss", "current_price": ctx.bar.close, "entry_price": ctx.position["entry_price"]})
            if ctx.position_direction() == "short" and ctx.bar.close > ctx.position["entry_price"] * (1 + self.METADATA["declared_sl_bps"] / 10000.0 * 0.75):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "preemptive_stop_loss", "current_price": ctx.bar.close, "entry_price": ctx.position["entry_price"]})
            return None # Hold position if no exit condition met

        # Entry logic: only consider entries within specific window
        if not (self.entry_hour_start <= current_hour < self.entry_hour_end):
            return None

        # Check if we already traded today (or in this 24h cycle for simplicity)
        # This prevents multiple entries within the same "day"
        if self.last_trade_bar > ctx.bar_index - 24: # Roughly one trade per 24 bars (day)
            return None

        # Calculate overnight session impulse (UTC 0-6)
        # We need data from the *previous* overnight session to make a decision
        # Find the closes from 0-6 UTC *yesterday* or earlier today if current hour is >6
        
        # To simplify, we'll just look at the price change over the last X hours (our "session")
        # ending *before* our entry window.
        
        # Let's define the "overnight session" as bars 6-12 hours ago (0-6 UTC if current is 6-12 UTC)
        # This is a simplification from a true UTC 0-6 window, but captures the spirit.
        
        # Get price from 6 hours ago (approx end of previous session)
        # and price from 12 hours ago (approx start of previous session)
        
        # For a 0-6 UTC session, if current time is 7 UTC:
        #   Session start: ctx.closes(7) -> index 0 (current bar) ... index 6 (bar 6 hours ago, 1 UTC) ... index 7 (bar 7 hours ago, 0 UTC)
        #   We need price at 0 UTC (7 bars ago) and price at 6 UTC (1 bar ago, if current is 7 UTC)
        
        # Let's use a simpler interpretation for "session impulse":
        # The change from 6 hours ago to 1 hour ago (representing the prior 5-hour session)
        # This avoids complex timestamp matching across days.
        
        # Price 6 hours ago (start of "overnight session" proxy)
        prev_session_start_price = ctx.closes(self.session_lookback_hours + 1)[0] # Oldest bar in lookback
        # Price 1 hour ago (end of "overnight session" proxy)
        prev_session_end_price = ctx.closes(2)[0] # Close of the bar before current

        # Impulse calculation
        impulse = prev_session_end_price - prev_session_start_price
        impulse_pct = (impulse / prev_session_start_price) * 100

        # Thresholds for impulse (tune these)
        long_impulse_threshold = 0.35 # 0.35% up over 6 hours
        short_impulse_threshold = -0.35 # 0.35% down over 6 hours

        signal = None
        confidence = 0.0

        if impulse_pct > long_impulse_threshold:
            # Check for follow-through: current bar is higher than previous session end
            if ctx.bar.close > prev_session_end_price:
                signal = "long"
                confidence = min(0.8, 0.5 + (impulse_pct - long_impulse_threshold) * 0.5)
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(signal, confidence=confidence, metadata={
                    "reason": "overnight_long_impulse_follow_through",
                    "impulse_pct": impulse_pct,
                    "prev_session_start_price": prev_session_start_price,
                    "prev_session_end_price": prev_session_end_price,
                    "current_price": ctx.bar.close,
                    "entry_hour": current_hour
                })
        elif impulse_pct < short_impulse_threshold:
            # Check for follow-through: current bar is lower than previous session end
            if ctx.bar.close < prev_session_end_price:
                signal = "short"
                confidence = min(0.8, 0.5 + (abs(impulse_pct) - abs(short_impulse_threshold)) * 0.5)
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(signal, confidence=confidence, metadata={
                    "reason": "overnight_short_impulse_follow_through",
                    "impulse_pct": impulse_pct,
                    "prev_session_start_price": prev_session_start_price,
                    "prev_session_end_price": prev_session_end_price,
                    "current_price": ctx.bar.close,
                    "entry_hour": current_hour
                })
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally, reset cooldown or adjust parameters based on performance
        pass