from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightGapReversion(Strategy):
    METADATA = {
        "name": "Overnight Gap Reversion BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hours, exit before US session
        "warmup_bars": 24, # Need a full day's data for session logic
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -9999
        self.cooldown_bars = 6 # Wait 6 hours after a trade
        self.prior_session_close = None
        self.session_open_price = None
        self.session_start_bar_index = -1
        self.gap_threshold_bps = 30.0 # Minimum gap to consider, 0.3%
        self.continuation_check_bars = 3 # Bars to check for continuation
        self.reversion_target_bps = 50.0 # How much of the gap to aim to revert
        self.max_hold_bars = 6 # Max bars to hold a position (until UTC 12, or 6 hours from 6am)

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(24) # Need enough data for prior day's close
        opens = ctx.opens(self.continuation_check_bars + 1)
        highs = ctx.highs(self.continuation_check_bars + 1)
        lows = ctx.lows(self.continuation_check_bars + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Session boundary: UTC 06:00 marks the start of the "day" for this strategy
        # We define prior session close as the close at UTC 05:00
        # And current session open as the open at UTC 06:00
        if current_hour == 6:
            # New session starts, record prior close and current open
            if len(closes) >= 2: # Ensure we have at least yesterday's 05:00 close
                self.prior_session_close = ctx.closes(2)[-2] # Close from 05:00 UTC
                self.session_open_price = ctx.bar.open
                self.session_start_bar_index = ctx.bar_index
            else:
                self.prior_session_close = None
                self.session_open_price = None
            return None # Do not trade on the session open bar itself

        if not ctx.has_position():
            if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
                return None # Cooldown active

            # Entry logic: Only consider entries between 07:00 and 10:00 UTC (after gap forms, before US session fully active)
            if 7 <= current_hour <= 10 and \
               self.prior_session_close is not None and \
               self.session_open_price is not None and \
               self.session_start_bar_index == ctx.bar_index - (current_hour - 6): # Ensure this is the current session

                gap_pct = ((self.session_open_price - self.prior_session_close) / self.prior_session_close) * 100
                gap_bps = gap_pct * 100

                # Check for gap without continuation
                if abs(gap_bps) > self.gap_threshold_bps:
                    # Check if price has moved *away* from the gap direction in the first few bars
                    # Or if it has failed to continue in the gap direction
                    if len(closes) > self.continuation_check_bars:
                        # Price action since session open (bar at 06:00)
                        recent_high = max(highs[-self.continuation_check_bars-1:])
                        recent_low = min(lows[-self.continuation_check_bars-1:])

                        # Long trade: Gap down, but no further significant drop (or slight recovery)
                        if gap_bps < -self.gap_threshold_bps: # Large gap down
                            # Check if price has not continued significantly lower
                            # and is now trying to revert (e.g., current price is above session open low)
                            if ctx.bar.close > self.session_open_price * (1 + gap_bps/20000) and \
                               ctx.bar.close > self.session_open_price: # Price has recovered past open
                                self.last_trade_bar = ctx.bar_index
                                target_price = self.prior_session_close * (1 - self.reversion_target_bps / 10000) # Target slightly below prior close
                                return ctx.signal(
                                    "long",
                                    confidence=0.7,
                                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                                    take_profit_bps=self.METADATA["declared_tp_bps"],
                                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                                    metadata={
                                        "reason": "overnight_gap_down_reversion",
                                        "gap_bps": round(gap_bps, 2),
                                        "prior_close": round(self.prior_session_close, 2),
                                        "session_open": round(self.session_open_price, 2),
                                        "current_close": round(ctx.bar.close, 2),
                                        "target_price": round(target_price, 2)
                                    }
                                )

                        # Short trade: Gap up, but no further significant rise (or slight pullback)
                        elif gap_bps > self.gap_threshold_bps: # Large gap up
                            # Check if price has not continued significantly higher
                            # and is now trying to revert (e.g., current price is below session open high)
                            if ctx.bar.close < self.session_open_price * (1 + gap_bps/20000) and \
                               ctx.bar.close < self.session_open_price: # Price has fallen below open
                                self.last_trade_bar = ctx.bar_index
                                target_price = self.prior_session_close * (1 + self.reversion_target_bps / 10000) # Target slightly above prior close
                                return ctx.signal(
                                    "short",
                                    confidence=0.7,
                                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                                    take_profit_bps=self.METADATA["declared_tp_bps"],
                                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                                    metadata={
                                        "reason": "overnight_gap_up_reversion",
                                        "gap_bps": round(gap_bps, 2),
                                        "prior_close": round(self.prior_session_close, 2),
                                        "session_open": round(self.session_open_price, 2),
                                        "current_close": round(ctx.bar.close, 2),
                                        "target_price": round(target_price, 2)
                                    }
                                )
        elif ctx.has_position():
            # Exit logic: Flat before US session (UTC 12:00) or if held too long
            # Or if price has reverted significantly towards the prior close
            if current_hour >= 12 or \
               (ctx.bar_index - self.last_trade_bar) >= self.max_hold_bars:

                if ctx.position_direction() == "long":
                    exit_reason = "max_hold_time_or_us_session_start_long"
                else:
                    exit_reason = "max_hold_time_or_us_session_start_short"

                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": exit_reason,
                        "current_hour": current_hour,
                        "bars_held": ctx.bar_index - self.last_trade_bar,
                        "current_close": round(ctx.bar.close, 2)
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally, could adjust cooldown or confidence based on outcome
        pass