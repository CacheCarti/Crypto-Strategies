from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionGapReversion(Strategy):
    METADATA = {
        "name": "USSessionGapReversionBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Max hold time until end of session
        "warmup_bars": 24, # Need at least 24 bars for prior day's close
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -100 # Cooldown period
        self.prior_us_session_close = None
        self.traded_today = False
        self.us_session_start_hour = 13 # UTC
        self.us_session_end_hour = 21 # UTC (exclusive, so trades close before this hour)
        self.gap_threshold_bps = 25.0 # Minimum gap size to consider
        self.continuation_bars = 2 # How many bars to check for continuation

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        opens = ctx.opens(self.METADATA["warmup_bars"] + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset daily trade flag at the start of a new day, before US session
        if current_hour == 0 and not self.traded_today:
            self.traded_today = False

        # End of US session - ensure position is flat
        if ctx.has_position() and current_hour >= self.us_session_end_hour:
            if ctx.position_direction() == "long":
                reason = "session_end_exit_long"
            else:
                reason = "session_end_exit_short"
            return ctx.signal("flat", confidence=0.7, metadata={"reason": reason, "current_hour": current_hour, "close": ctx.bar.close})

        # Capture prior US session close
        if current_hour == self.us_session_end_hour - 1: # Last bar of US session
            self.prior_us_session_close = ctx.bar.close

        # Entry logic within US session
        if self.us_session_start_hour <= current_hour < self.us_session_end_hour and not self.traded_today:
            if self.prior_us_session_close is None:
                return None

            # Only evaluate at the start of the US session or shortly after
            if current_hour == self.us_session_start_hour:
                session_open = ctx.bar.open
                gap_bps = (session_open - self.prior_us_session_close) / self.prior_us_session_close * 10000

                if abs(gap_bps) > self.gap_threshold_bps:
                    # Check for lack of continuation
                    # If gap up, check if price failed to continue higher in the first few bars
                    # If gap down, check if price failed to continue lower in the first few bars
                    
                    # Need enough bars to check continuation
                    if len(closes) < self.continuation_bars + 1:
                        return None
                    
                    # Check for continuation over the first 'continuation_bars' bars of the session
                    # We are on the first bar of the session (current_hour == self.us_session_start_hour)
                    # So we need to look at the close of the current bar and potentially the next few.
                    # Given this is on_bar, we can only see up to the current bar's close.
                    # A simple check: if the current bar's close is already reversing the gap.

                    if gap_bps > 0: # Gap up
                        # If current close is below open (red bar) or not significantly above open, implies lack of strong continuation
                        # And price is moving back towards the prior close
                        if ctx.bar.close < session_open and ctx.bar.close < self.prior_us_session_close * (1 + self.gap_threshold_bps/2 * 0.0001): # Reversing gap
                            if not ctx.has_position() and ctx.bar_index > self.last_trade_bar + 12: # Cooldown of 12 bars
                                self.traded_today = True
                                return ctx.signal("short", confidence=0.7,
                                                  stop_loss_bps=self.METADATA["declared_sl_bps"],
                                                  take_profit_bps=self.METADATA["declared_tp_bps"],
                                                  horizon_seconds= (self.us_session_end_hour - current_hour) * 3600,
                                                  metadata={"reason": "us_session_gap_up_reversal",
                                                            "gap_bps": round(gap_bps, 2),
                                                            "prior_close": round(self.prior_us_session_close, 2),
                                                            "session_open": round(session_open, 2),
                                                            "current_close": round(ctx.bar.close, 2)})
                    elif gap_bps < 0: # Gap down
                        # If current close is above open (green bar) or not significantly below open, implies lack of strong continuation
                        # And price is moving back towards the prior close
                        if ctx.bar.close > session_open and ctx.bar.close > self.prior_us_session_close * (1 - self.gap_threshold_bps/2 * 0.0001): # Reversing gap
                            if not ctx.has_position() and ctx.bar_index > self.last_trade_bar + 12: # Cooldown of 12 bars
                                self.traded_today = True
                                return ctx.signal("long", confidence=0.7,
                                                  stop_loss_bps=self.METADATA["declared_sl_bps"],
                                                  take_profit_bps=self.METADATA["declared_tp_bps"],
                                                  horizon_seconds= (self.us_session_end_hour - current_hour) * 3600,
                                                  metadata={"reason": "us_session_gap_down_reversal",
                                                            "gap_bps": round(gap_bps, 2),
                                                            "prior_close": round(self.prior_us_session_close, 2),
                                                            "session_open": round(session_open, 2),
                                                            "current_close": round(ctx.bar.close, 2)})
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        # self.traded_today is reset at midnight, not on position close, to ensure only one trade per session.