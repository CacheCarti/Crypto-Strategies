from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionGapReversion(Strategy):
    METADATA = {
        "name": "USSessionGapReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold is within session, ~4 hours max
        "warmup_bars": 24, # Need enough bars for prior session close
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13  # UTC 13:00
        self.us_session_end_hour = 21    # UTC 21:00 (exclusive, so trade until 20:xx)
        self.prior_session_close = None
        self.last_trade_bar_index = -100 # Cooldown
        self.cooldown_bars = 6
        self.gap_threshold_bps = 30.0 # Minimum gap to consider
        self.no_continuation_threshold_bps = 10.0 # How much price should move against gap
        self.max_entry_hour = 17 # Don't enter too late in the session

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        if len(closes) < self.METADATA["warmup_bars"] + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Update prior session close at the end of the US session
        if current_hour == self.us_session_end_hour - 1: # The last bar of the US session
            self.prior_session_close = ctx.bar.close

        # Check for trade entry conditions
        if (current_hour >= self.us_session_start_hour and
            current_hour < self.max_entry_hour and
            not ctx.has_position() and
            ctx.bar_index > self.last_trade_bar_index + self.cooldown_bars and
            self.prior_session_close is not None):

            session_open_price = ctx.bar.open
            gap_bps = ((session_open_price - self.prior_session_close) / self.prior_session_close) * 10000

            # Long trade: Gap down, but no further continuation
            if gap_bps < -self.gap_threshold_bps: # Significant gap down
                # Check if price has moved back up somewhat, indicating lack of continuation
                # We check the current bar's close vs its open as a simple 'no continuation' proxy
                # Or, check if current price is above the session open (recovering)
                # For a more robust check, we need to see if it *failed* to go lower
                low_since_open = min(ctx.lows(current_hour - self.us_session_start_hour + 1)) if current_hour > self.us_session_start_hour else ctx.bar.low
                if low_since_open >= session_open_price * (1 - self.no_continuation_threshold_bps / 10000.0) and ctx.bar.close > session_open_price:
                    # Price gapped down, but current close is above open, suggesting rejection of lower prices
                    # Or, price has not significantly gone lower than open and is now recovering.
                    # We are looking for the close to be moving back towards the prior session close
                    if ctx.bar.close > session_open_price + (self.prior_session_close - session_open_price) * 0.2: # Some recovery
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.us_session_end_hour * 3600 - ctx.bar.timestamp.timestamp(), # Exit before session end
                            metadata={
                                "reason": "us_session_gap_down_reversion",
                                "prior_session_close": self.prior_session_close,
                                "session_open": session_open_price,
                                "gap_bps": gap_bps,
                                "current_close": ctx.bar.close,
                                "entry_hour": current_hour
                            }
                        )

            # Short trade: Gap up, but no further continuation
            elif gap_bps > self.gap_threshold_bps: # Significant gap up
                # Check if price has moved back down somewhat, indicating lack of continuation
                high_since_open = max(ctx.highs(current_hour - self.us_session_start_hour + 1)) if current_hour > self.us_session_start_hour else ctx.bar.high
                if high_since_open <= session_open_price * (1 + self.no_continuation_threshold_bps / 10000.0) and ctx.bar.close < session_open_price:
                    # Price gapped up, but current close is below open, suggesting rejection of higher prices
                    if ctx.bar.close < session_open_price - (session_open_price - self.prior_session_close) * 0.2: # Some reversion
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.us_session_end_hour * 3600 - ctx.bar.timestamp.timestamp(), # Exit before session end
                            metadata={
                                "reason": "us_session_gap_up_reversion",
                                "prior_session_close": self.prior_session_close,
                                "session_open": session_open_price,
                                "gap_bps": gap_bps,
                                "current_close": ctx.bar.close,
                                "entry_hour": current_hour
                            }
                        )

        # Exit logic: if we are in a position and it's getting late in the session
        if ctx.has_position() and current_hour >= self.us_session_end_hour - 1: # Exit on the second to last hour or later
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": "end_of_session_exit",
                    "current_hour": current_hour,
                    "position_direction": ctx.position_direction(),
                    "entry_price": ctx.position["entry_price"]
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade index to enforce cooldown, even if position closed early
        self.last_trade_bar_index = ctx.bar_index
        pass # No adaptive logic for now