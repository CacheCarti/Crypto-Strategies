from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthAsiaGapReversion(Strategy):
    METADATA = {
        "name": "ETH Asia Gap Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold for 1h bars, but exit before session close
        "warmup_bars": 50, # Need prior session data
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -self.METADATA["warmup_bars"] # Allow trading immediately after warmup
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.min_gap_bps = 50.0 # Minimum gap size to consider for a trade
        self.gap_continuation_bars = 4 # Number of bars to check for continuation after gap
        self.max_gap_continuation_pct = 0.25 # Max percentage continuation before invalidating gap
        self.previous_asia_close = None
        self.asia_session_open_price = None
        self.session_gap_direction = None # "up" or "down"
        self.session_gap_magnitude = 0.0 # in bps

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        
        # --- Session Boundary Detection and Gap Calculation ---
        # Asia session: UTC 0-8 (9 hours total, 0-7 are within, 8 is the close)
        # We need the close of the prior session to compare with the open of the current session.
        
        # At UTC 00:00 (start of Asia session), we check the gap from previous session close
        # to the current bar's open.
        if current_hour == 0:
            if self.previous_asia_close is None:
                # Need at least one full session to establish previous_asia_close
                # Calculate previous Asia session close (UTC 8 of previous day)
                # This is a simplification; in a real scenario, you'd need the close
                # of the bar at 08:00 UTC from the previous day.
                # For simplicity, we'll use the close of the bar just before 00:00 UTC.
                if len(closes) >= 2: # Need at least two bars for prior close
                    self.previous_asia_close = closes[-2] # Close of the last bar of previous day
                else:
                    return None # Not enough history yet

            self.asia_session_open_price = ctx.bar.open
            gap_raw = self.asia_session_open_price - self.previous_asia_close
            self.session_gap_magnitude = (abs(gap_raw) / self.previous_asia_close) * 10000 # in bps

            if self.session_gap_magnitude >= self.min_gap_bps:
                self.session_gap_direction = "up" if gap_raw > 0 else "down"
            else:
                self.session_gap_direction = None
                self.session_gap_magnitude = 0.0
            
            # Reset continuation check
            self.gap_continuation_checked = False
            self.has_traded_this_session = False # Only one trade per session
            self.first_bar_of_asia_session = ctx.bar_index
        
        # Update previous_asia_close at the end of the Asia session (UTC 8:00)
        if current_hour == 8:
            self.previous_asia_close = ctx.bar.close
            self.session_gap_direction = None # Reset gap state for next session
            self.session_gap_magnitude = 0.0
            self.asia_session_open_price = None

        # --- Exit Logic ---
        if ctx.has_position():
            # Exit before the end of the Asia session (e.g., at 07:00 UTC to be safe)
            if current_hour >= 7 and ctx.position_direction() is not None:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "exit_before_asia_session_close",
                    "current_price": ctx.bar.close,
                    "hour": current_hour
                })
            
            # Structure stop/reversion target check (simplified: target previous_asia_close)
            if self.session_gap_direction == "up" and ctx.position_direction() == "short":
                if ctx.bar.close <= self.previous_asia_close:
                    return ctx.signal("flat", confidence=0.9, metadata={
                        "reason": "short_reversion_target_hit",
                        "target_price": self.previous_asia_close,
                        "current_price": ctx.bar.close
                    })
            elif self.session_gap_direction == "down" and ctx.position_direction() == "long":
                if ctx.bar.close >= self.previous_asia_close:
                    return ctx.signal("flat", confidence=0.9, metadata={
                        "reason": "long_reversion_target_hit",
                        "target_price": self.previous_asia_close,
                        "current_price": ctx.bar.close
                    })
            return None # Hold position

        # --- Entry Logic ---
        # Only consider entries within Asia session and after cooldown
        if not (0 <= current_hour < 7): # Trade between 00:00 and 06:59 UTC
            return None
        
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None
        
        if self.has_traded_this_session:
            return None
        
        # Check if we have a valid gap from the start of the session
        if self.session_gap_direction is None or self.session_gap_magnitude == 0.0:
            return None

        # Check for lack of continuation for `gap_continuation_bars` after the gap open
        if ctx.bar_index >= self.first_bar_of_asia_session + self.gap_continuation_bars:
            if not getattr(self, 'gap_continuation_checked', False):
                # Calculate price change since session open
                price_change = ctx.bar.close - self.asia_session_open_price
                continuation_bps = (abs(price_change) / self.asia_session_open_price) * 10000

                # If the price has continued too far in the gap direction, invalidate the setup
                if continuation_bps > self.session_gap_magnitude * self.max_gap_continuation_pct:
                    self.session_gap_direction = None # Invalidate gap
                    self.session_gap_magnitude = 0.0
                    self.gap_continuation_checked = True # Only check once
                    return None
                self.gap_continuation_checked = True # Mark as checked

            # If gap was up, look for short to revert
            if self.session_gap_direction == "up":
                # Check for a slight pullback from the session open high to indicate reversal potential
                # Or simply trade if the continuation check passed
                if ctx.bar.close < self.asia_session_open_price: # Price moved below session open
                    self.last_trade_bar = ctx.bar_index
                    self.has_traded_this_session = True
                    return ctx.signal("short", confidence=0.75, metadata={
                        "reason": "asia_session_up_gap_reversion",
                        "gap_direction": "up",
                        "gap_magnitude_bps": self.session_gap_magnitude,
                        "session_open_price": self.asia_session_open_price,
                        "current_price": ctx.bar.close
                    })
            
            # If gap was down, look for long to revert
            elif self.session_gap_direction == "down":
                # Check for a slight bounce from the session open low to indicate reversal potential
                if ctx.bar.close > self.asia_session_open_price: # Price moved above session open
                    self.last_trade_bar = ctx.bar_index
                    self.has_traded_this_session = True
                    return ctx.signal("long", confidence=0.75, metadata={
                        "reason": "asia_session_down_gap_reversion",
                        "gap_direction": "down",
                        "gap_magnitude_bps": self.session_gap_magnitude,
                        "session_open_price": self.asia_session_open_price,
                        "current_price": ctx.bar.close
                    })
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position details or adapt strategy based on performance
        pass