from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightGapReversion(Strategy):
    METADATA = {
        "name": "OvernightGapReversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Roughly 4 hours, aiming to close before next session
        "warmup_bars": 24, # Need at least 24 bars to get prior day's close
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -1
        self.cooldown_bars = 6 # Wait 6 bars (6 hours) after a trade
        self.prior_session_close = None
        self.overnight_gap_threshold_bps = 30.0 # Gap must be at least 30 bps to consider
        self.reversion_target_bps = 50.0 # Target 50 bps of reversion from gap

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday=0, Sunday=6

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # --- Regime Filters ---
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        # Only trade in 'normal' or 'fear' regimes, avoid extreme greed/fear
        if fear_greed_index > 70 or fear_greed_index < 30:
            return None

        # Only trade during normal trend regimes, avoid crisis
        if ctx.market.get("regime", "NORMAL") not in ["NORMAL", "HIGH_VOL"]:
            return None
        
        # Avoid trading on weekends (Saturday=5, Sunday=6)
        if current_weekday >= 5:
            return None

        # --- Session Logic ---
        # Define overnight session (e.g., 00:00 UTC to 06:00 UTC)
        # US session (e.g., 13:00 UTC to 21:00 UTC) - we want to exit before this
        
        # Detect the close of the prior session (e.g., 23:00 UTC)
        if current_hour == 23:
            self.prior_session_close = ctx.bar.close
            return None # Don't trade, just record the close

        # Entry logic: overnight window (00:00 UTC to 06:00 UTC)
        if 0 <= current_hour <= 6 and self.prior_session_close is not None:
            # Only consider entering once per day during this window
            if ctx.bar.timestamp.day != (ctx.bar.timestamp - ctx.bar.timestamp.resolution).day:
                # New day, reset prior session close for the day
                self.prior_session_close = None # Ensure we only use the *most recent* 23:00 close

            if self.prior_session_close is not None:
                gap_bps = ((ctx.bar.open - self.prior_session_close) / self.prior_session_close) * 10000

                # Long signal: Significant negative gap (oversold overnight)
                if gap_bps < -self.overnight_gap_threshold_bps and not ctx.has_position():
                    # Look for some initial bounce before confirming
                    if ctx.bar.close > ctx.bar.open: # Current bar is green
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            take_profit_bps=self.reversion_target_bps,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            metadata={
                                "reason": "overnight_negative_gap_reversion_long",
                                "gap_bps": round(gap_bps, 2),
                                "prior_session_close": round(self.prior_session_close, 2),
                                "current_open": round(ctx.bar.open, 2),
                                "fear_greed_index": fear_greed_index,
                            }
                        )

                # Short signal: Significant positive gap (overbought overnight)
                elif gap_bps > self.overnight_gap_threshold_bps and not ctx.has_position():
                    # Look for some initial pullback before confirming
                    if ctx.bar.close < ctx.bar.open: # Current bar is red
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            take_profit_bps=self.reversion_target_bps,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            metadata={
                                "reason": "overnight_positive_gap_reversion_short",
                                "gap_bps": round(gap_bps, 2),
                                "prior_session_close": round(self.prior_session_close, 2),
                                "current_open": round(ctx.bar.open, 2),
                                "fear_greed_index": fear_greed_index,
                            }
                        )
            self.prior_session_close = None # Reset after attempting to trade for the day

        # Exit logic: If we have a position, attempt to exit before the next US session (e.g., 12:00 UTC)
        if ctx.has_position() and current_hour >= 12:
            direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]
            
            # If we are profitable, or if we are nearing the next session, flatten
            if (direction == "long" and ctx.bar.close > entry_price) or \
               (direction == "short" and ctx.bar.close < entry_price) or \
               current_hour >= 12: # Force exit if approaching US session
                
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "session_end_exit",
                        "current_hour": current_hour,
                        "direction": direction,
                        "entry_price": round(entry_price, 2),
                        "current_price": round(ctx.bar.close, 2),
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to enforce cooldown
        self.last_trade_bar = ctx.bar_index