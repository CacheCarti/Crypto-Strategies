from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionReversion(Strategy):
    METADATA = {
        "name": "US Session Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2, # Aim for 2-hour hold
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 18 # UTC 18:00
        self.entry_hour_end = 23   # UTC 23:00 (last bar starting at 23:00)
        self.exit_hour = 0         # Exit before UTC 00:00
        self.min_gap_bps = 50      # Minimum gap to consider for reversion
        self.cooldown_bars = 4     # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store prior session's close and current session's open
        self.prior_session_close: Optional[float] = None
        self.current_session_open: Optional[float] = None
        self.last_bar_day = -1

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        opens = ctx.opens(self.METADATA["warmup_bars"] + 1)
        
        if len(closes) < self.METADATA["warmup_bars"] + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Update prior session close and current session open daily
        if current_day != self.last_bar_day:
            # A new day has started, reset session data
            # The prior session close would be yesterday's last bar close.
            # For simplicity, let's assume the "session" concept here is a daily one
            # and we are looking for a gap between yesterday's close and today's open.
            # We'll use the close from the end of the previous day (UTC 23:00 or 00:00 depending on exact definition)
            # For this strategy, let's simplify and define "prior session close" as the close of the last bar of the previous day (23:00 UTC)
            # and "current session open" as the open of the first bar of the current day (00:00 UTC).
            # This is a bit tricky with 1-hour bars, as we only see the current bar's open/close.
            # Let's adjust: prior_session_close will be the close of the 23:00 UTC bar from the previous day.
            # current_session_open will be the open of the 00:00 UTC bar of the current day.
            
            # This requires looking back to the previous day's 23:00 UTC bar.
            # Given ctx.closes() only provides a history, not specific time lookups,
            # we need to track this state more explicitly.
            # Let's simplify the "session" to be "the day before".
            # The prior_session_close will be the close of the 23:00 UTC bar of the previous day.
            # We can only compute this after the 23:00 bar has passed.
            
            # For this specific strategy, we need the *previous* day's close (23:00 UTC bar)
            # and the *current* day's open (00:00 UTC bar).
            # The current bar is ctx.bar. This means when ctx.bar.timestamp.hour is 0,
            # we have the open for the new day. The close for the previous day would be
            # the close of the bar that STARTED at 23:00 UTC yesterday.

            if current_hour == 0: # This is the first bar of a new day (00:00 UTC)
                if len(closes) > 2: # Need at least 2 bars: current (00:00) and previous (23:00)
                    self.prior_session_close = closes[-2] # Close of the 23:00 UTC bar yesterday
                    self.current_session_open = ctx.bar.open # Open of the 00:00 UTC bar today
                else:
                    self.prior_session_close = None
                    self.current_session_open = None
            else: # Not the first bar of the day, so no new gap to evaluate yet
                pass
            self.last_bar_day = current_day

        # Check for position exit conditions first
        if ctx.has_position():
            # Exit if it's nearing the end of the session (before 00:00 UTC)
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None # Hold position

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic within the specified US session hours
        if self.entry_hour_start <= current_hour <= self.entry_hour_end:
            if self.prior_session_close is not None and self.current_session_open is not None:
                gap_bps = ((self.current_session_open - self.prior_session_close) / self.prior_session_close) * 10000

                # Check for a "large" gap
                if abs(gap_bps) > self.min_gap_bps:
                    # Check for lack of continuation - price moving back towards prior close
                    # We need a simple way to check "lack of continuation"
                    # One way: current price is moving in the opposite direction of the gap.
                    # Another way: current price is closer to the prior_session_close than the current_session_open.
                    
                    # Let's use a simple moving average to check for reversal of gap direction.
                    # If the gap was positive (up), we look for current price to be below a short SMA
                    # If the gap was negative (down), we look for current price to be above a short SMA
                    
                    sma_period = 3 # Short SMA for continuation check
                    sma = self._sma(closes, sma_period)
                    
                    if sma is None:
                        return None

                    if gap_bps > 0: # Gap up
                        # We want to short if the market gapped up but is now trading below the SMA,
                        # implying a move back towards the prior close.
                        if ctx.bar.close < sma and ctx.bar.close < self.current_session_open:
                            # And current price is also significantly below the open, indicating weakness
                            # We want to fade the gap. Target is the prior_session_close.
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal("short", confidence=0.65,
                                              metadata={"reason": "gap_up_reversion",
                                                        "gap_bps": round(gap_bps, 2),
                                                        "prior_close": round(self.prior_session_close, 2),
                                                        "session_open": round(self.current_session_open, 2),
                                                        "current_close": round(ctx.bar.close, 2),
                                                        "sma_val": round(sma, 2),
                                                        "target_price": round(self.prior_session_close, 2)
                                                        })
                    elif gap_bps < 0: # Gap down
                        # We want to long if the market gapped down but is now trading above the SMA,
                        # implying a move back towards the prior close.
                        if ctx.bar.close > sma and ctx.bar.close > self.current_session_open:
                            # And current price is also significantly above the open, indicating strength
                            # We want to fade the gap. Target is the prior_session_close.
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal("long", confidence=0.65,
                                              metadata={"reason": "gap_down_reversion",
                                                        "gap_bps": round(gap_bps, 2),
                                                        "prior_close": round(self.prior_session_close, 2),
                                                        "session_open": round(self.current_session_open, 2),
                                                        "current_close": round(ctx.bar.close, 2),
                                                        "sma_val": round(sma, 2),
                                                        "target_price": round(self.prior_session_close, 2)
                                                        })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Adaptive sizing or logging based on position outcome
        # For now, just logging for visibility
        pass