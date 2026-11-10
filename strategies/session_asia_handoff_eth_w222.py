from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionImpulseStrategy(Strategy):
    METADATA = {
        "name": "Asia Session Impulse",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aiming for 1-2 hours max
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 0  # UTC 00:00
        self.entry_hour_end = 8    # UTC 08:00 (Asia session roughly)
        self.exit_hour = 8         # Exit before UTC 08:00
        self.min_session_range_bps = 50  # Minimum range for a valid impulse
        self.cooldown_bars = 5     # Cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars

        # Store session open/high/low/close for impulse calculation
        self.session_open_price = None
        self.session_high_price = None
        self.session_low_price = None
        self.session_start_bar_index = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # Cooldown logic
        if current_bar_index - self.last_trade_bar_index < self.cooldown_bars:
            # If we have an open position, let it ride. If not, wait for cooldown.
            if not ctx.has_position():
                return None

        # --- Session Tracking ---
        # Reset session data at the start of a new Asia session
        if current_hour == self.entry_hour_start and (self.session_start_bar_index is None or ctx.bar_index > self.session_start_bar_index):
            self.session_open_price = ctx.bar.open
            self.session_high_price = ctx.bar.high
            self.session_low_price = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
        elif self.session_start_bar_index is not None and ctx.bar_index > self.session_start_bar_index:
            # Update session high/low within the session
            if self.session_high_price is not None:
                self.session_high_price = max(self.session_high_price, ctx.bar.high)
            if self.session_low_price is not None:
                self.session_low_price = min(self.session_low_price, ctx.bar.low)

        # --- Entry Logic ---
        if not ctx.has_position() and current_hour >= self.entry_hour_start and current_hour < self.entry_hour_end:
            if self.session_open_price is not None and self.session_high_price is not None and self.session_low_price is not None:
                session_range_bps = (self.session_high_price - self.session_low_price) / self.session_open_price * 10000
                
                # Only consider impulse if session has some range
                if session_range_bps >= self.min_session_range_bps:
                    # Calculate current impulse relative to session open
                    current_price = ctx.bar.close
                    impulse_up_bps = (current_price - self.session_open_price) / self.session_open_price * 10000
                    impulse_down_bps = (self.session_open_price - current_price) / self.session_open_price * 10000

                    # Look for a clear directional impulse within the session
                    # We want to enter on a continuation of the session's direction
                    # and ensure the price is near the session high/low for conviction.
                    
                    # Long signal: price is significantly above session open and near session high
                    if impulse_up_bps > self.min_session_range_bps / 2 and (self.session_high_price - current_price) / self.session_open_price * 10000 < self.min_session_range_bps / 4:
                        self.last_trade_bar_index = current_bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            metadata={
                                "reason": "asia_session_impulse_long",
                                "session_open": self.session_open_price,
                                "session_high": self.session_high_price,
                                "session_low": self.session_low_price,
                                "current_price": current_price,
                                "impulse_up_bps": impulse_up_bps,
                                "session_range_bps": session_range_bps
                            }
                        )
                    # Short signal: price is significantly below session open and near session low
                    elif impulse_down_bps > self.min_session_range_bps / 2 and (current_price - self.session_low_price) / self.session_open_price * 10000 < self.min_session_range_bps / 4:
                        self.last_trade_bar_index = current_bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            metadata={
                                "reason": "asia_session_impulse_short",
                                "session_open": self.session_open_price,
                                "session_high": self.session_high_price,
                                "session_low": self.session_low_price,
                                "current_price": current_price,
                                "impulse_down_bps": impulse_down_bps,
                                "session_range_bps": session_range_bps
                            }
                        )

        # --- Exit Logic ---
        if ctx.has_position():
            # Exit near the end of the Asia session regardless of profit/loss
            if current_hour >= self.exit_hour:
                self.last_trade_bar_index = current_bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "session_end_exit",
                        "current_hour": current_hour,
                        "exit_hour": self.exit_hour,
                        "current_price": ctx.bar.close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session data on position close if it helps, or just log.
        # For this strategy, session data is reset by hour, not by trade.
        pass