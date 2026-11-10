from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionGapReversion(Strategy):
    METADATA = {
        "name": "SOL Session Gap Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold is up to 4 hours, or until session close
        "warmup_bars": 50, # Need enough bars for prior session close
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -9999
        self.cooldown_bars = 10 # Cooldown after a trade
        self.session_open_price = None
        self.prior_session_close_price = None
        self.gap_threshold_bps = 50.0 # Minimum gap to consider, in bps
        self.no_continuation_threshold_bps = 20.0 # How much price should move against the gap for "no continuation"
        self.max_gap_bps = 300.0 # Avoid trading extremely large gaps

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        opens = ctx.opens(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Session Definition:
        # Late US Session: UTC 18-24 (inclusive of 18, exclusive of 0 next day)
        # Prior Session Close: The close of the bar at UTC 17:00 (i.e., 17th hour bar's close)
        # Current Session Open: The open of the bar at UTC 18:00 (i.e., 18th hour bar's open)

        # Detect the start of the late US session (UTC 18:00)
        if current_hour == 18:
            if ctx.bar_index > 0 and ctx.bar_index > self.last_trade_bar + self.cooldown_bars:
                # Get prior session close (close of 17:00 bar)
                # Check if we have enough history to get the previous hour's close
                if len(closes) >= 2:
                    self.prior_session_close_price = closes[-2]
                    self.session_open_price = ctx.bar.open
                    
                    # Calculate gap
                    if self.prior_session_close_price is not None:
                        gap_bps = (self.session_open_price - self.prior_session_close_price) / self.prior_session_close_price * 10000
                        
                        # Only consider gaps within a reasonable range
                        if abs(gap_bps) >= self.gap_threshold_bps and abs(gap_bps) <= self.max_gap_bps:
                            # Store gap info for later evaluation within the session
                            self.current_session_gap_info = {
                                "direction": "up" if gap_bps > 0 else "down",
                                "size_bps": gap_bps,
                                "prior_close": self.prior_session_close_price,
                                "session_open": self.session_open_price,
                                "gap_bar_index": ctx.bar_index
                            }
                        else:
                            self.current_session_gap_info = None
                    else:
                        self.current_session_gap_info = None
            else:
                self.current_session_gap_info = None # Reset if still in cooldown or not enough history
        
        # Trading logic within the late US session (18-23 UTC)
        if 18 <= current_hour <= 23 and self.current_session_gap_info:
            if ctx.has_position():
                # If position is open, check if it's time to exit due to session end
                if current_hour == 23 and ctx.position_direction() == "long": # Exit longs before 0 UTC
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "session_end_exit_long",
                        "current_price": ctx.bar.close,
                        "session_hour": current_hour
                    })
                elif current_hour == 23 and ctx.position_direction() == "short": # Exit shorts before 0 UTC
                     return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "session_end_exit_short",
                        "current_price": ctx.bar.close,
                        "session_hour": current_hour
                    })
                return None # Hold position
            
            # Entry logic
            if ctx.bar_index > self.current_session_gap_info["gap_bar_index"] + 1: # Wait at least one bar after gap forms
                if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
                    return None # Still in cooldown from a previous trade

                # Regime Filters
                trend_regime = ctx.market.get("trend_regime", "neutral")
                crisis_score = ctx.market.get("crisis_score", 0.0)
                fear_greed = ctx.features.get("fear_greed_index", 50)
                
                # Stand down in crisis or extreme fear/greed
                if crisis_score > 0.5 or fear_greed < 20 or fear_greed > 80:
                    return None

                gap_direction = self.current_session_gap_info["direction"]
                prior_close = self.current_session_gap_info["prior_close"]
                session_open = self.current_session_gap_info["session_open"]
                
                # Check for "no continuation"
                if gap_direction == "up": # Gap up, looking for short
                    # Price moves down significantly from session open, towards prior close
                    if ctx.bar.close < session_open - (session_open * self.no_continuation_threshold_bps / 10000):
                        # Ensure we are still above or near the prior close to have room for reversion
                        if ctx.bar.close > prior_close:
                            # Only short if not in a strong uptrend
                            if trend_regime != "bull":
                                self.last_trade_bar = ctx.bar_index
                                self.current_session_gap_info = None # Only trade once per gap setup
                                return ctx.signal(
                                    "short",
                                    confidence=0.75,
                                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                                    take_profit_bps=self.METADATA["declared_tp_bps"],
                                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                                    metadata={
                                        "reason": "late_us_gap_reversion_short",
                                        "gap_direction": "up",
                                        "gap_size_bps": self.current_session_gap_info["size_bps"],
                                        "prior_close": prior_close,
                                        "session_open": session_open,
                                        "current_close": ctx.bar.close,
                                        "trend_regime": trend_regime,
                                        "fear_greed": fear_greed
                                    }
                                )
                elif gap_direction == "down": # Gap down, looking for long
                    # Price moves up significantly from session open, towards prior close
                    if ctx.bar.close > session_open + (session_open * self.no_continuation_threshold_bps / 10000):
                        # Ensure we are still below or near the prior close to have room for reversion
                        if ctx.bar.close < prior_close:
                            # Only long if not in a strong downtrend
                            if trend_regime != "bear":
                                self.last_trade_bar = ctx.bar_index
                                self.current_session_gap_info = None # Only trade once per gap setup
                                return ctx.signal(
                                    "long",
                                    confidence=0.75,
                                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                                    take_profit_bps=self.METADATA["declared_tp_bps"],
                                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                                    metadata={
                                        "reason": "late_us_gap_reversion_long",
                                        "gap_direction": "down",
                                        "gap_size_bps": self.current_session_gap_info["size_bps"],
                                        "prior_close": prior_close,
                                        "session_open": session_open,
                                        "current_close": ctx.bar.close,
                                        "trend_regime": trend_regime,
                                        "fear_greed": fear_greed
                                    }
                                )
        
        # Reset gap info at the end of the session or outside trading hours
        if current_hour == 0: # Start of a new day, clear all session state
            self.prior_session_close_price = None
            self.session_open_price = None
            self.current_session_gap_info = None

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for debugging/analysis
        pass