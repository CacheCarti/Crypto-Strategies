from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionGapReversion(Strategy):
    METADATA = {
        "name": "Asia Session Gap Reversion (SOL)",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, targeting 1-4 bars hold
        "warmup_bars": 50, # Need enough bars for 2-3 sessions
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 00:00 (Asia session starts)
        self.session_end_hour = 8    # UTC 08:00 (Asia session ends)
        self.min_gap_bps = 50.0      # Minimum gap size in basis points to consider a trade
        self.reversion_threshold_pct = 0.3 # Percentage of gap to reverse before entry
        self.cooldown_bars = 4       # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        self.last_asia_close = None
        self.traded_today = False # Track if we've traded in the current Asia session

        # For gap tracking, we need the closing price of the previous session
        # and the opening price of the current session.
        self.previous_session_close = None
        self.current_session_open = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close
        current_open = ctx.bar.open

        # --- Session Management ---
        # If we are at the start of a new Asia session (00:00 UTC)
        if current_hour == self.session_start_hour:
            if not self.traded_today: # This block runs once per session start
                self.previous_session_close = closes[-2] if len(closes) >= 2 else None
                self.traded_today = True # Mark that we have processed this session's start
                # We need to wait for the actual bar.open of the session to get the gap.
                # The first bar of the session (hour 0) will have its open as the current_open.
                self.current_session_open = current_open

        # Reset traded_today flag at the end of the Asia session (after 08:00 UTC)
        if current_hour == self.session_end_hour:
            self.traded_today = False # Allow trading in the next session

        # --- Exit Logic ---
        if ctx.has_position():
            # Exit before the Asia session ends if still in position
            if current_hour >= self.session_end_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_end_exit", "current_hour": current_hour}
                )
            # Exit if stop loss or take profit hit (managed by engine), or if
            # market moves significantly against the gap fill.
            # No explicit exit logic here, rely on declared SL/TP and session end.
            return None

        # --- Entry Logic ---
        # Only consider entries during the Asia session and if not on cooldown
        if not (self.session_start_hour <= current_hour < self.session_end_hour):
            return None

        if ctx.bar_index - self.last_trade_bar <= self.cooldown_bars:
            return None

        if self.previous_session_close is None or self.current_session_open is None:
            return None

        # Calculate gap
        gap_bps = (self.current_session_open - self.previous_session_close) / self.previous_session_close * 10000

        if abs(gap_bps) < self.min_gap_bps:
            return None # Gap is too small to be significant

        # Check for reversion
        if gap_bps > 0: # Gap Up: Look for short
            # Price should have initially pushed higher, then reversed downwards
            # and current price should be below a certain threshold of the gap.
            # We want to short if the price has moved back down by a percentage of the gap.
            if current_close < self.current_session_open - (abs(gap_bps) / 10000 * self.previous_session_close * self.reversion_threshold_pct):
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "asia_gap_up_reversion",
                            "gap_bps": gap_bps,
                            "prev_session_close": self.previous_session_close,
                            "current_session_open": self.current_session_open,
                            "current_close": current_close,
                            "reversion_threshold_pct": self.reversion_threshold_pct
                        }
                    )
        elif gap_bps < 0: # Gap Down: Look for long
            # Price should have initially pushed lower, then reversed upwards
            # and current price should be above a certain threshold of the gap.
            # We want to long if the price has moved back up by a percentage of the gap.
            if current_close > self.current_session_open + (abs(gap_bps) / 10000 * self.previous_session_close * self.reversion_threshold_pct):
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "asia_gap_down_reversion",
                            "gap_bps": gap_bps,
                            "prev_session_close": self.previous_session_close,
                            "current_session_open": self.current_session_open,
                            "current_close": current_close,
                            "reversion_threshold_pct": self.reversion_threshold_pct
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive sizing
        # For now, just reset the traded_today flag if it was an early exit,
        # but the strategy already has a daily flag reset.
        pass