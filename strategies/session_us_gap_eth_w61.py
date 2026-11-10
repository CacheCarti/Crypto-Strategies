from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapReversion(Strategy):
    METADATA = {
        "name": "Session Gap Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for 1-hour session hold
        "warmup_bars": 24, # Enough for 24-hour lookback
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -self.METADATA["warmup_bars"] # Allow trading immediately after warmup
        self.cooldown_bars = 4 # Wait 4 bars after a trade before entering again
        self.prior_session_close = None
        self.session_open_price = None
        self.session_high = None
        self.session_low = None
        self.session_start_bar_index = -1
        self.entered_this_session = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        if len(closes) < self.METADATA["warmup_bars"] + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # Detect start of US session (UTC 13:00)
        # We consider the bar from 13:00 to 13:59 as the start of the session
        if current_hour == 13 and (self.session_start_bar_index == -1 or current_bar_index > self.session_start_bar_index):
            # New session started
            if self.session_open_price is not None:
                # Store prior session's close if available (from the bar just before 13:00)
                # The close of the bar ending at 12:59 UTC (i.e. the 12th hour bar)
                self.prior_session_close = ctx.closes(2)[0] if len(ctx.closes(2)) > 1 else None
            
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_bar_index = current_bar_index
            self.entered_this_session = False
            # ctx.logger.info(f"New US session started at {ctx.bar.timestamp}. Open: {self.session_open_price}, Prior Close: {self.prior_session_close}")

        # Update session high/low
        if current_bar_index >= self.session_start_bar_index:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Exit logic for any open position
        if ctx.has_position():
            # Exit before 21:00 UTC (i.e., on the bar ending at 20:59)
            if current_hour >= 21:
                # ctx.logger.info(f"Exiting position at {ctx.bar.timestamp} due to session end.")
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "exit_hour": current_hour})
            
            # Simple structure stop: if price breaks session high/low too much
            position_direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]
            
            if position_direction == "long":
                # If price drops below session low after entry, consider exiting
                if ctx.bar.low < entry_price * (1 - self.METADATA["declared_sl_bps"] / 10000.0):
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_structure_stop_loss", "entry_price": entry_price, "current_low": ctx.bar.low})
            elif position_direction == "short":
                # If price rises above session high after entry, consider exiting
                if ctx.bar.high > entry_price * (1 + self.METADATA["declared_sl_bps"] / 10000.0):
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_structure_stop_loss", "entry_price": entry_price, "current_high": ctx.bar.high})
            
            return None # Hold existing position

        # Entry logic: Only trade during US session, if not already entered, and after cooldown
        if not self.entered_this_session and \
           current_hour >= 13 and current_hour < 21 and \
           current_bar_index > self.last_trade_bar + self.cooldown_bars and \
           self.prior_session_close is not None and \
           self.session_open_price is not None:

            # Calculate gap size relative to prior session close
            gap_bps = (self.session_open_price - self.prior_session_close) / self.prior_session_close * 10000.0
            
            # Look for large gaps that show signs of mean reversion (lack of immediate continuation)
            # A "large gap" could be, say, > 50 bps (0.5%) or < -50 bps
            # "Lack of continuation" means the current price is moving back towards the prior close
            
            # Long entry: Significant gap down, but current price is recovering
            if gap_bps < -50.0: # Large gap down
                # If current price is above session open and moving towards prior close
                if ctx.bar.close > self.session_open_price and ctx.bar.close < self.prior_session_close:
                    # And current bar is not making new session lows
                    if ctx.bar.low > self.session_low:
                        self.entered_this_session = True
                        self.last_trade_bar = current_bar_index
                        # ctx.logger.info(f"Long signal at {ctx.bar.timestamp}. Gap: {gap_bps:.2f} bps. Close: {ctx.bar.close}, Open: {self.session_open_price}, Prior Close: {self.prior_session_close}")
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "session_gap_down_reversion",
                                "gap_bps": gap_bps,
                                "current_close": ctx.bar.close,
                                "session_open": self.session_open_price,
                                "prior_session_close": self.prior_session_close,
                                "entry_hour": current_hour
                            }
                        )
            
            # Short entry: Significant gap up, but current price is falling
            elif gap_bps > 50.0: # Large gap up
                # If current price is below session open and moving towards prior close
                if ctx.bar.close < self.session_open_price and ctx.bar.close > self.prior_session_close:
                    # And current bar is not making new session highs
                    if ctx.bar.high < self.session_high:
                        self.entered_this_session = True
                        self.last_trade_bar = current_bar_index
                        # ctx.logger.info(f"Short signal at {ctx.bar.timestamp}. Gap: {gap_bps:.2f} bps. Close: {ctx.bar.close}, Open: {self.session_open_price}, Prior Close: {self.prior_session_close}")
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "session_gap_up_reversion",
                                "gap_bps": gap_bps,
                                "current_close": ctx.bar.close,
                                "session_open": self.session_open_price,
                                "prior_session_close": self.prior_session_close,
                                "entry_hour": current_hour
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy on close, but good to have
        pass