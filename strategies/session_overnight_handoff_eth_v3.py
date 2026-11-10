from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthOvernightHandoff(Strategy):
    METADATA = {
        "name": "EthOvernightHandoff",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Roughly 6 hours
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20 # For EMA and volume
        self.entry_cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.entry_cooldown_bars

        # State to track for session logic
        self.overnight_entry_price = None
        self.overnight_direction = None
        self.session_impulse_detected_bar = -1
        self.session_impulse_threshold = 0.005 # 0.5% move

        # Parameters for EMA crossover
        self.fast_ema_period = 8
        self.slow_ema_period = 21

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
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Calculate EMAs for crossover logic
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.entry_cooldown_bars:
            return None

        # --- Session Logic ---
        # Overnight window (UTC 0-6) to detect impulse
        if 0 <= current_hour < 6:
            # If we don't have an overnight entry price, establish it at the start of the window
            if self.overnight_entry_price is None:
                self.overnight_entry_price = ctx.bar.close
                self.overnight_direction = None # Reset direction
                self.session_impulse_detected_bar = -1 # Reset impulse detection

            # Check for impulse within the overnight window
            if self.overnight_entry_price is not None:
                price_change = (ctx.bar.close - self.overnight_entry_price) / self.overnight_entry_price
                if price_change > self.session_impulse_threshold:
                    self.overnight_direction = "long"
                    self.session_impulse_detected_bar = ctx.bar_index
                elif price_change < -self.session_impulse_threshold:
                    self.overnight_direction = "short"
                    self.session_impulse_detected_bar = ctx.bar_index

        # Handoff window (UTC 6-12) for follow-through entry
        elif 6 <= current_hour < 12:
            if not ctx.has_position() and self.overnight_direction is not None and self.session_impulse_detected_bar != -1:
                # Check for EMA crossover as follow-through confirmation
                # Ensure it's a fresh crossover, not just staying above/below
                prev_fast_ema = self._ema(closes[:-1], self.fast_ema_period)
                prev_slow_ema = self._ema(closes[:-1], self.slow_ema_period)

                if prev_fast_ema is None or prev_slow_ema is None:
                    return None

                if self.overnight_direction == "long":
                    # Long entry: EMA fast crosses above EMA slow
                    if fast_ema > slow_ema and prev_fast_ema <= prev_slow_ema:
                        self.last_trade_bar = ctx.bar_index
                        self.overnight_entry_price = None # Reset for next session
                        self.overnight_direction = None
                        self.session_impulse_detected_bar = -1
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            metadata={
                                "reason": "overnight_long_impulse_ema_cross_follow_through",
                                "entry_hour": current_hour,
                                "fast_ema": fast_ema,
                                "slow_ema": slow_ema,
                                "price_change_from_overnight_open": (ctx.bar.close - self.overnight_entry_price) / self.overnight_entry_price,
                            }
                        )
                elif self.overnight_direction == "short":
                    # Short entry: EMA fast crosses below EMA slow
                    if fast_ema < slow_ema and prev_fast_ema >= prev_slow_ema:
                        self.last_trade_bar = ctx.bar_index
                        self.overnight_entry_price = None # Reset for next session
                        self.overnight_direction = None
                        self.session_impulse_detected_bar = -1
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            metadata={
                                "reason": "overnight_short_impulse_ema_cross_follow_through",
                                "entry_hour": current_hour,
                                "fast_ema": fast_ema,
                                "slow_ema": slow_ema,
                                "price_change_from_overnight_open": (ctx.bar.close - self.overnight_entry_price) / self.overnight_entry_price,
                            }
                        )

        # Exit logic: Close any open position before the next US session (e.g., 12-18 UTC)
        # Or if a position is held past the handoff window
        if ctx.has_position() and current_hour >= 12:
            direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]
            return_bps = (ctx.bar.close - entry_price) / entry_price * 10000 if direction == "long" else (entry_price - ctx.bar.close) / entry_price * 10000
            self.overnight_entry_price = None # Reset for next session
            self.overnight_direction = None
            self.session_impulse_detected_bar = -1
            return ctx.signal(
                "flat",
                confidence=0.8,
                metadata={
                    "reason": "session_boundary_exit",
                    "exit_hour": current_hour,
                    "return_bps_at_exit": return_bps,
                }
            )

        # Reset overnight tracking for the next day if we've passed the handoff window
        if current_hour >= 12 and self.overnight_entry_price is not None:
            self.overnight_entry_price = None
            self.overnight_direction = None
            self.session_impulse_detected_bar = -1

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close, regardless of profit/loss
        self.last_trade_bar = ctx.bar_index
        pass # No adaptive sizing for this strategy currently