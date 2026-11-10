from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionGapReversion(Strategy):
    METADATA = {
        "name": "USSessionGapReversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, targeting 1 bar hold
        "warmup_bars": 50,
        "required_features": [], # No specific features needed for this price-action based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13  # UTC 13:00
        self.us_session_end_hour = 21    # UTC 21:00 (exclusive, so last bar is 20:00)
        self.last_trade_bar_index = -100 # Cooldown
        self.last_session_close = None
        self.session_open_price = None
        self.session_gap_direction = None # "long" or "short"
        self.session_gap_magnitude = 0.0 # Percentage difference
        self.cooldown_bars = 3

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for position exit conditions
        if ctx.has_position():
            # Exit if past the session end hour
            if current_hour >= self.us_session_end_hour:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_end_exit",
                    "current_hour": current_hour,
                    "entry_bar": ctx.position["entry_bar_index"]
                })
            # If position has been held for too long (e.g., 2 bars), exit
            if ctx.bar_index - ctx.position["entry_bar_index"] >= 2:
                 return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "time_based_exit",
                    "bars_held": ctx.bar_index - ctx.position["entry_bar_index"]
                })
            return None # Hold position if conditions not met

        # Cooldown period after a trade or if already in a position
        if ctx.bar_index < self.last_trade_bar_index + self.cooldown_bars:
            return None

        # --- Session Logic ---
        # US session starts at 13:00 UTC. The bar at 13:00 is the session open.
        # The bar at 12:00 UTC is the prior session's close.
        if current_hour == self.us_session_start_hour:
            # This is the first bar of the US session
            # Calculate prior session's close (the close of the 12:00 UTC bar)
            if len(closes) >= 2:
                self.last_session_close = closes[-2] # Close of the previous bar (12:00 UTC)
            else:
                self.last_session_close = None # Not enough history

            self.session_open_price = ctx.bar.open
            self.session_gap_direction = None
            self.session_gap_magnitude = 0.0

            if self.last_session_close is not None:
                gap_pct = ((self.session_open_price - self.last_session_close) / self.last_session_close) * 100
                
                # Define a significant gap threshold (e.g., 0.2% - adjust based on backtest)
                gap_threshold_pct = 0.2

                if gap_pct > gap_threshold_pct:
                    self.session_gap_direction = "long" # Open is higher than prior close (gap up)
                    self.session_gap_magnitude = abs(gap_pct)
                elif gap_pct < -gap_threshold_pct:
                    self.session_gap_direction = "short" # Open is lower than prior close (gap down)
                    self.session_gap_magnitude = abs(gap_pct)
            
            # Reset for the day
            self.last_trade_bar_index = ctx.bar_index # Prevent trading immediately on session open
            return None # Do not trade on the session open bar itself

        # Trading logic during the US session (after the open bar, before end hour)
        if (current_hour > self.us_session_start_hour and current_hour < self.us_session_end_hour
            and self.session_gap_direction is not None and self.session_gap_magnitude > 0):

            # Regime filter: Only trade in 'normal' or 'volatile' regimes, avoid 'crisis' or 'meltdown'
            current_regime = ctx.market.get("regime", "NORMAL")
            if current_regime in ["CRISIS", "MELTDOWN"]:
                return None # Stand down in extreme market conditions

            # Trend filter: Only trade against weak trends or in neutral trends
            trend_regime = ctx.market.get("trend_regime", "neutral")
            if (self.session_gap_direction == "long" and trend_regime == "bull" and ctx.market.get("trend_regime_confidence", 0.0) > 0.5):
                return None # Don't short a strong bull trend
            if (self.session_gap_direction == "short" and trend_regime == "bear" and ctx.market.get("trend_regime_confidence", 0.0) > 0.5):
                return None # Don't long a strong bear trend


            # Look for lack of continuation and reversion
            current_price = ctx.bar.close
            
            # If gap up, we expect price to move lower to fill the gap
            if self.session_gap_direction == "long":
                # Check if price has moved lower from the open, indicating gap fill attempt
                # AND if it's below the session open, to confirm lack of continuation
                if current_price < self.session_open_price and current_price < self.last_session_close:
                    # We want to short if the gap up is failing and reversing
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("short", confidence=0.6, metadata={
                        "reason": "us_session_gap_up_reversion",
                        "gap_direction": "long",
                        "gap_magnitude_pct": self.session_gap_magnitude,
                        "session_open": self.session_open_price,
                        "prior_session_close": self.last_session_close,
                        "current_price": current_price,
                        "regime": current_regime,
                        "trend_regime": trend_regime
                    })
            
            # If gap down, we expect price to move higher to fill the gap
            elif self.session_gap_direction == "short":
                # Check if price has moved higher from the open, indicating gap fill attempt
                # AND if it's above the session open, to confirm lack of continuation
                if current_price > self.session_open_price and current_price > self.last_session_close:
                    # We want to long if the gap down is failing and reversing
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("long", confidence=0.6, metadata={
                        "reason": "us_session_gap_down_reversion",
                        "gap_direction": "short",
                        "gap_magnitude_pct": self.session_gap_magnitude,
                        "session_open": self.session_open_price,
                        "prior_session_close": self.last_session_close,
                        "current_price": current_price,
                        "regime": current_regime,
                        "trend_regime": trend_regime
                    })
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt strategy based on close outcome
        pass