from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionHandoffETH(Strategy):
    METADATA = {
        "name": "Session Handoff ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold for one session, 1 hour bars
        "warmup_bars": 30, # Need enough bars for 20-period indicators
        "required_features": [], # No specific external features used, relying on OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 18 # UTC 18:00
        self.entry_hour_end = 24   # UTC 23:00 (exclusive, so 18, 19, 20, 21, 22, 23)
        self.exit_hour = 0         # UTC 00:00 (exit before this bar)
        self.session_length_bars = 6 # 6 hours for the late US session (18-23)
        self.momentum_period = 6 # 6 bars = 6 hours
        self.cooldown_bars = 3    # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit logic for existing position
        if ctx.has_position():
            # If we are past the exit hour (UTC 00:00) or close to it, flatten
            if current_hour >= self.exit_hour and current_hour < self.entry_hour_start:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_boundary_exit",
                    "current_hour": current_hour,
                    "entry_hour_start": self.entry_hour_start,
                    "exit_hour": self.exit_hour
                })
            return None # Hold position if not time to exit

        # Entry logic - only during late US session
        if not (self.entry_hour_start <= current_hour < self.entry_hour_end):
            return None

        # Calculate momentum over the session length
        # Need at least self.momentum_period bars for the session impulse
        if len(closes) < self.momentum_period:
            return None

        # Calculate impulse for the session
        # We look at the change from the beginning of the potential session window
        # to the current bar.
        # Let's say we are at 20:00 UTC. We want to see the direction from 18:00 UTC.
        # This means we need the open at 18:00 and the current close.
        # We use the closes for simplicity, comparing current close to `momentum_period` bars ago.
        
        session_start_price = closes[-self.momentum_period]
        current_price = ctx.bar.close

        # Simple directional impulse: check if current price is significantly different
        # from the price at the beginning of the session period.
        # We use a threshold to avoid trading on flat price action.
        price_change_pct = ((current_price - session_start_price) / session_start_price) * 100

        # Define a small threshold for impulse detection (e.g., 0.1% move)
        impulse_threshold_pct = 0.1

        signal_direction = None
        reason = ""
        confidence = 0.0

        if price_change_pct > impulse_threshold_pct:
            signal_direction = "long"
            reason = "late_us_session_bullish_impulse"
            confidence = min(0.7, 0.5 + (price_change_pct / (impulse_threshold_pct * 10))) # Scale confidence
        elif price_change_pct < -impulse_threshold_pct:
            signal_direction = "short"
            reason = "late_us_session_bearish_impulse"
            confidence = min(0.7, 0.5 + (abs(price_change_pct) / (impulse_threshold_pct * 10))) # Scale confidence

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "price_change_pct": price_change_pct,
                    "session_start_price": session_start_price,
                    "current_price": current_price,
                    "impulse_threshold_pct": impulse_threshold_pct
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy currently, just logging
        pass