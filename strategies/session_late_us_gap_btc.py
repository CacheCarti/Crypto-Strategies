from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapReversionBTC(Strategy):
    METADATA = {
        "name": "BTC Session Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 24, # Need at least 24 bars for prior day close
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -9999
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.entry_hour_start = 18 # UTC 18:00 (late US session)
        self.entry_hour_end = 23 # UTC 23:00
        self.exit_hour = 0 # Exit before UTC 0:00 (new day)
        self.prior_session_close = None
        self.session_open_price = None
        self.session_high_price = None
        self.session_low_price = None
        self.session_start_bar_index = -1
        self.traded_today = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(2)
        opens = ctx.opens(2)
        if len(closes) < 2:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset session data at the start of a new session (e.g., UTC 0)
        if current_hour == 0 and not self.traded_today:
            # Store prior session's close for potential gap calculation
            self.prior_session_close = closes[-1]
            self.session_open_price = ctx.bar.open
            self.session_high_price = ctx.bar.high
            self.session_low_price = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
            self.traded_today = False # Reset daily trade flag
            return None # No trading at session start

        # Update session high/low
        if ctx.bar_index > self.session_start_bar_index:
            self.session_high_price = max(self.session_high_price, ctx.bar.high)
            self.session_low_price = min(self.session_low_price, ctx.bar.low)

        # Exit logic for open positions
        if ctx.has_position():
            if current_hour >= self.exit_hour and current_hour < self.entry_hour_start: # Exit before new session or when time is up
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

            # Structure stop: if price goes beyond the original gap direction significantly
            pos = ctx.position
            if pos["direction"] == "long":
                # If we are long (expecting price to go up towards prior close), and it breaks session low
                if ctx.bar.low < self.session_low_price * 0.995: # 0.5% below session low
                     return ctx.signal("flat", confidence=0.8, metadata={"reason": "long_structure_stop_break_low", "current_low": ctx.bar.low, "session_low": self.session_low_price})
            elif pos["direction"] == "short":
                # If we are short (expecting price to go down towards prior close), and it breaks session high
                if ctx.bar.high > self.session_high_price * 1.005: # 0.5% above session high
                    return ctx.signal("flat", confidence=0.8, metadata={"reason": "short_structure_stop_break_high", "current_high": ctx.bar.high, "session_high": self.session_high_price})


        # Entry logic: Only trade if no position, within entry hours, and not on cooldown, and not traded today
        if ctx.has_position() or \
           ctx.bar_index - self.last_trade_bar < self.cooldown_bars or \
           not (self.entry_hour_start <= current_hour <= self.entry_hour_end) or \
           self.traded_today or \
           self.prior_session_close is None or \
           self.session_open_price is None:
            return None

        # Calculate gap and potential reversion
        gap_magnitude_bps = ((self.session_open_price - self.prior_session_close) / self.prior_session_close) * 10000

        # Only consider larger gaps
        min_gap_bps = 50.0 # 0.5% gap
        if abs(gap_magnitude_bps) < min_gap_bps:
            return None

        # Check for lack of continuation and reversion opportunity
        signal = None
        confidence = 0.65

        # Bearish gap (open much lower than prior close)
        if gap_magnitude_bps < -min_gap_bps:
            # If price has started to recover towards the prior close, but not fully, and current bar is bullish
            # Entry: Gap down, but price is now above session open and moving up
            if ctx.bar.close > self.session_open_price and ctx.bar.close > closes[-1]:
                # Check if it has not fully closed the gap yet
                if ctx.bar.close < self.prior_session_close * 0.999: # Not fully closed, still room to run
                    signal = "long"
                    self.traded_today = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        signal,
                        confidence=confidence,
                        metadata={
                            "reason": "bearish_gap_reversion_long",
                            "gap_bps": round(gap_magnitude_bps, 2),
                            "prior_close": self.prior_session_close,
                            "session_open": self.session_open_price,
                            "current_close": ctx.bar.close
                        }
                    )

        # Bullish gap (open much higher than prior close)
        elif gap_magnitude_bps > min_gap_bps:
            # If price has started to drop towards the prior close, but not fully, and current bar is bearish
            # Entry: Gap up, but price is now below session open and moving down
            if ctx.bar.close < self.session_open_price and ctx.bar.close < closes[-1]:
                # Check if it has not fully closed the gap yet
                if ctx.bar.close > self.prior_session_close * 1.001: # Not fully closed, still room to run
                    signal = "short"
                    self.traded_today = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        signal,
                        confidence=confidence,
                        metadata={
                            "reason": "bullish_gap_reversion_short",
                            "gap_bps": round(gap_magnitude_bps, 2),
                            "prior_close": self.prior_session_close,
                            "session_open": self.session_open_price,
                            "current_close": ctx.bar.close
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset traded_today flag if position closes outside of session, or if we want to allow another trade later in session (optional)
        # For this strategy, we keep traded_today = True to enforce one trade per day/session
        pass