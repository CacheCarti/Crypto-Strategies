from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightGapReversion(Strategy):
    METADATA = {
        "name": "Overnight Gap Reversion BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hour holds
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.gap_threshold_bps = 20.0  # Minimum gap size in bps to consider
        self.continuation_threshold_bps = 10.0 # How much price moves away from gap fill
        self.cooldown_bars = 6
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade
        self.prior_session_close = None
        self.last_session_day = -1 # To track daily sessions

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        opens = ctx.opens(50)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Session Definition:
        # Overnight session: UTC 0-6 (6 bars for 1h timeframe)
        # US/European session: UTC 7-23

        # Detect new session day (for resetting prior close)
        if current_day != self.last_session_day:
            self.prior_session_close = None
            self.last_session_day = current_day

        # Capture prior session close at the end of the US/European session (UTC 23:00)
        if current_hour == 23 and self.prior_session_close is None:
            self.prior_session_close = ctx.bar.close
            return None # Don't trade on the closing bar

        # Trading logic during overnight session (UTC 0-6)
        if 0 <= current_hour <= 6 and self.prior_session_close is not None:
            if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
                return None

            if ctx.has_position():
                # If position is open, check if it's time to exit before US session
                if current_hour >= 6: # Exit by end of overnight session
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "overnight_session_end", "current_hour": current_hour})
                return None # Hold position during overnight session

            # Check for gap and lack of continuation
            gap_bps = ((opens[-1] - self.prior_session_close) / self.prior_session_close) * 10000
            current_price_change_from_open_bps = ((ctx.bar.close - opens[-1]) / opens[-1]) * 10000

            # Long condition: Gap down and no further downside continuation (reverting to prior close)
            if gap_bps < -self.gap_threshold_bps: # Significant gap down
                # Check if price is not continuing lower, or starting to reverse
                if current_price_change_from_open_bps > -self.continuation_threshold_bps: # Not continuing down significantly
                    # Also check if current price is still below prior close (room to revert)
                    if ctx.bar.close < self.prior_session_close:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("long",
                                          confidence=0.75,
                                          stop_loss_bps=self.METADATA["declared_sl_bps"],
                                          take_profit_bps=self.METADATA["declared_tp_bps"],
                                          horizon_seconds=self.METADATA["declared_hold_seconds"],
                                          metadata={
                                              "reason": "overnight_gap_down_reversion",
                                              "gap_bps": round(gap_bps, 2),
                                              "prior_close": round(self.prior_session_close, 2),
                                              "current_close": round(ctx.bar.close, 2),
                                              "open_price": round(opens[-1], 2),
                                              "continuation_bps": round(current_price_change_from_open_bps, 2)
                                          })

            # Short condition: Gap up and no further upside continuation (reverting to prior close)
            elif gap_bps > self.gap_threshold_bps: # Significant gap up
                # Check if price is not continuing higher, or starting to reverse
                if current_price_change_from_open_bps < self.continuation_threshold_bps: # Not continuing up significantly
                    # Also check if current price is still above prior close (room to revert)
                    if ctx.bar.close > self.prior_session_close:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("short",
                                          confidence=0.75,
                                          stop_loss_bps=self.METADATA["declared_sl_bps"],
                                          take_profit_bps=self.METADATA["declared_tp_bps"],
                                          horizon_seconds=self.METADATA["declared_hold_seconds"],
                                          metadata={
                                              "reason": "overnight_gap_up_reversion",
                                              "gap_bps": round(gap_bps, 2),
                                              "prior_close": round(self.prior_session_close, 2),
                                              "current_close": round(ctx.bar.close, 2),
                                              "open_price": round(opens[-1], 2),
                                              "continuation_bps": round(current_price_change_from_open_bps, 2)
                                          })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for debugging/analysis
        pass