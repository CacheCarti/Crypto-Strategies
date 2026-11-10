from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapReversion(Strategy):
    METADATA = {
        "name": "Session Gap Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aiming for ~1 hour hold, potentially longer for gap fill
        "warmup_bars": 24, # Need enough bars for daily open/close logic
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_us_session_close = None
        self.last_trade_bar_index = -100 # Cooldown period
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade
        self.session_open_price = None
        self.session_open_bar_index = -1
        self.traded_today = False # Track if we've traded in the current UTC day

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset traded_today flag at the start of a new day
        if ctx.bar_index > 0 and ctx.bar.timestamp.day != ctx.closes(2)[0].day:
            self.traded_today = False

        # --- US Session Logic (UTC 13-21) ---
        # Capture US session close (UTC 21:00 bar)
        if current_hour == 21 and self.last_us_session_close is None:
            self.last_us_session_close = ctx.bar.close

        # Reset last_us_session_close at the start of a new day (if it hasn't been set for the previous day)
        # Or, if it's past 21:00 and we haven't seen a new 21:00 close yet, clear it.
        if current_hour == 0: # Start of a new UTC day
            self.last_us_session_close = None

        # Identify US session open (UTC 13:00 bar)
        if current_hour == 13 and self.session_open_bar_index != ctx.bar_index:
            self.session_open_price = ctx.bar.open
            self.session_open_bar_index = ctx.bar_index
            # Ensure last_us_session_close is from the *previous* day for gap calculation
            # This is a bit tricky with 1-hour bars. We need the 21:00 close from the previous day.
            # Let's assume self.last_us_session_close holds the *most recent* 21:00 close seen.
            # If current_hour is 13, and last_us_session_close was set yesterday at 21:00, it's valid.

        # --- Trading Logic: US Session Gap Reversion ---
        if (ctx.bar_index - self.last_trade_bar_index) < self.cooldown_bars:
            return None # Cooldown active

        if ctx.has_position():
            # If we are in a position, and it's getting close to the end of the US session, consider exiting
            if current_hour >= 20 and ctx.position_direction() is not None: # Exit before 21:00 close
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None

        if self.traded_today:
            return None # Only one trade per day/session

        if self.last_us_session_close is not None and self.session_open_price is not None and current_hour >= 13 and current_hour < 20:
            gap_size = self.session_open_price - self.last_us_session_close
            gap_bps = (gap_size / self.last_us_session_close) * 10000

            # Define a significant gap threshold (e.g., 50 bps)
            min_gap_bps = 40.0
            reversion_threshold_bps = 20.0 # How much price needs to move against the gap

            # Long signal: Significant gap down, price starts moving up but hasn't filled the gap
            if gap_bps < -min_gap_bps: # Gap down
                # If price is now above session open and moving towards prior close
                if ctx.bar.close > self.session_open_price and (self.last_us_session_close - ctx.bar.close) / self.last_us_session_close * 10000 > reversion_threshold_bps:
                    # Ensure current price is still below prior session close
                    if ctx.bar.close < self.last_us_session_close:
                        self.last_trade_bar_index = ctx.bar_index
                        self.traded_today = True
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            metadata={
                                "reason": "us_session_gap_down_reversion",
                                "gap_bps": round(gap_bps, 2),
                                "session_open": round(self.session_open_price, 2),
                                "prior_session_close": round(self.last_us_session_close, 2),
                                "current_close": round(ctx.bar.close, 2),
                                "current_hour": current_hour
                            }
                        )

            # Short signal: Significant gap up, price starts moving down but hasn't filled the gap
            elif gap_bps > min_gap_bps: # Gap up
                # If price is now below session open and moving towards prior close
                if ctx.bar.close < self.session_open_price and (ctx.bar.close - self.last_us_session_close) / self.last_us_session_close * 10000 < -reversion_threshold_bps:
                    # Ensure current price is still above prior session close
                    if ctx.bar.close > self.last_us_session_close:
                        self.last_trade_bar_index = ctx.bar_index
                        self.traded_today = True
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            metadata={
                                "reason": "us_session_gap_up_reversion",
                                "gap_bps": round(gap_bps, 2),
                                "session_open": round(self.session_open_price, 2),
                                "prior_session_close": round(self.last_us_session_close, 2),
                                "current_close": round(ctx.bar.close, 2),
                                "current_hour": current_hour
                            }
                        )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, allowing a new trade after cooldown_bars
        self.last_trade_bar_index = ctx.bar_index
        # We don't reset traded_today here because it's a daily flag.
        pass