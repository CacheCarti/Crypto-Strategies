from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionBreakout(Strategy):
    METADATA = {
        "name": "Asia Session Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2,  # Intended to hold through next session handoff
        "warmup_bars": 30,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_high = -1.0
        self.session_low = float('inf')
        self.session_start_bar_index = -1
        self.last_signal_bar_index = -1
        self.cooldown_bars = 5 # Cooldown after a trade
        self.traded_today = False # Track if a trade was made today

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset session data at the start of the Asia session (UTC 0)
        if current_hour == 0 and not self.traded_today:
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
            self.traded_today = False
        elif current_hour == 0 and self.traded_today:
            # If we traded today and it's the start of a new day (UTC 0), reset traded_today
            # This handles cases where a position might have closed late in the previous day
            self.traded_today = False
            self.session_high = ctx.bar.high # Re-initialize for the new session
            self.session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index

        # Update session high/low during Asia session (UTC 0-8)
        if 0 <= current_hour < 8:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
        
        # Cooldown check
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # --- Entry Logic (after Asia session handoff, UTC 8-10) ---
        # Look for breakout after the Asia session closes, but before it gets too late
        if 8 <= current_hour < 10 and not ctx.has_position() and not self.traded_today:
            # Ensure we have valid session boundaries
            if self.session_high > self.session_low and self.session_start_bar_index != -1:
                # Long entry: Break above session high
                if ctx.bar.close > self.session_high:
                    self.last_signal_bar_index = ctx.bar_index
                    self.traded_today = True
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "asia_session_breakout_long",
                            "session_high": self.session_high,
                            "session_low": self.session_low,
                            "current_close": ctx.bar.close,
                            "entry_hour": current_hour,
                        }
                    )
                # Short entry: Break below session low
                elif ctx.bar.close < self.session_low:
                    self.last_signal_bar_index = ctx.bar_index
                    self.traded_today = True
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "asia_session_breakout_short",
                            "session_high": self.session_high,
                            "session_low": self.session_low,
                            "current_close": ctx.bar.close,
                            "entry_hour": current_hour,
                        }
                    )
        
        # --- Exit Logic (close before UTC 8 the next day, or if in position and it's too late) ---
        # Close any open position if it's nearing the end of the trading window (e.g., UTC 7 next day)
        # Or if we entered and it's already past the entry window, consider closing if not profitable enough
        if ctx.has_position():
            # Mandatory exit before UTC 8 to avoid holding through the next Asia session
            if current_hour >= 7 and ctx.position_direction() == "long":
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_based_exit_pre_asia_long",
                        "current_hour": current_hour,
                        "entry_price": ctx.position["entry_price"],
                        "current_price": ctx.bar.close,
                    }
                )
            elif current_hour >= 7 and ctx.position_direction() == "short":
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_based_exit_pre_asia_short",
                        "current_hour": current_hour,
                        "entry_price": ctx.position["entry_price"],
                        "current_price": ctx.bar.close,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any daily specific state if needed, though 'traded_today' is handled in on_bar for 0 UTC
        pass