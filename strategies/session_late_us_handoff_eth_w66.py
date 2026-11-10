from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthSessionImpulse(Strategy):
    METADATA = {
        "name": "ETH Session Impulse",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for up to 6 hours
        "warmup_bars": 30,
        "required_features": [], # No external features used
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 18  # UTC 18 (2 PM EST)
        self.entry_hour_end = 23    # UTC 23 (6 PM EST) - last bar for entry is 23:00
        self.exit_hour = 0          # UTC 0 (7 PM EST) - exit before this hour
        self.min_impulse_strength = 0.003 # 0.3% move in 5 bars to qualify
        self.cooldown_bars = 6      # Cooldown for 6 hours after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: Close position before self.exit_hour
        if ctx.has_position() and current_hour == self.exit_hour:
            direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]
            return ctx.signal(
                "flat",
                confidence=0.8,
                metadata={
                    "reason": "session_boundary_exit",
                    "exit_hour": self.exit_hour,
                    "current_hour": current_hour,
                    "direction": direction,
                    "entry_price": entry_price,
                    "exit_price": ctx.bar.close
                }
            )

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: Only consider entries within the defined session
        if self.entry_hour_start <= current_hour <= self.entry_hour_end:
            # Only enter if no position is currently open
            if ctx.has_position():
                return None

            # Calculate impulse over the last 5 bars (5 hours)
            impulse_period = 5
            if len(closes) < impulse_period:
                return None

            session_open_price = closes[-impulse_period]
            session_close_price = ctx.bar.close
            
            impulse_pct = (session_close_price - session_open_price) / session_open_price

            signal_direction = None
            reason = ""
            confidence = 0.0

            if impulse_pct > self.min_impulse_strength:
                signal_direction = "long"
                reason = "late_us_session_bullish_impulse"
                confidence = min(0.7, 0.5 + (impulse_pct / (self.min_impulse_strength * 2))) # Scale confidence
            elif impulse_pct < -self.min_impulse_strength:
                signal_direction = "short"
                reason = "late_us_session_bearish_impulse"
                confidence = min(0.7, 0.5 + (abs(impulse_pct) / (self.min_impulse_strength * 2))) # Scale confidence
            
            if signal_direction:
                self.last_trade_bar = ctx.bar_index # Record trade bar for cooldown
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": reason,
                        "impulse_pct": impulse_pct,
                        "session_open_price": session_open_price,
                        "current_price": ctx.bar.close,
                        "entry_hour": current_hour,
                        "impulse_period": impulse_period
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally, log or adapt based on position outcome
        pass