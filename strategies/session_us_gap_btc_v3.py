from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionGapReversion(Strategy):
    METADATA = {
        "name": "US Session Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, potentially hold for a few bars
        "warmup_bars": 50, # Need enough bars for EMA and prior session data
        "required_features": ["btc_return_pct"] # For confirming lack of strong trend
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.gap_threshold_pct = 0.003 # 0.3% gap
        self.continuation_threshold_pct = 0.001 # 0.1% continuation
        self.cooldown_bars = 3 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # State for session tracking
        self.prior_us_session_close = None
        self.last_us_session_end_bar = -1
        self.last_us_session_start_bar = -1

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period * 2) # Need enough for EMA
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # Check for US session boundaries (UTC 13-21)
        # US session starts at 13:00 UTC, ends at 21:00 UTC (last bar is 20:00-21:00)
        # We are looking for the bar that closes at 13:00 UTC (i.e. 12:00-13:00 bar) to mark start
        # And the bar that closes at 21:00 UTC (i.e. 20:00-21:00 bar) to mark end.

        # Logic to capture prior US session close
        if current_hour == 21 and self.last_us_session_end_bar != current_bar_index:
            # End of US session. Store its close.
            self.prior_us_session_close = ctx.bar.close
            self.last_us_session_end_bar = current_bar_index
            # Reset last_us_session_start_bar to ensure we capture a new start
            self.last_us_session_start_bar = -1 

        # Logic to detect start of new US session
        is_us_session_start_bar = (current_hour == 13 and self.last_us_session_start_bar != current_bar_index)

        # Exit logic: Flat before US session closes, or if current position is too old
        if ctx.has_position():
            # If it's the last bar of the US session (20:00-21:00 UTC), flat out
            if current_hour == 20:
                if ctx.position["direction"] == "long":
                    reason = "exit_us_session_end_long"
                else:
                    reason = "exit_us_session_end_short"
                return ctx.signal("flat", confidence=0.7, metadata={"reason": reason, "current_hour": current_hour, "bar_index": current_bar_index})
            
            # If position has been held for too long (e.g., 4 bars)
            if current_bar_index - self.last_trade_bar > 4: # Max 4 hour hold
                if ctx.position["direction"] == "long":
                    reason = "exit_long_hold_time"
                else:
                    reason = "exit_short_hold_time"
                return ctx.signal("flat", confidence=0.5, metadata={"reason": reason, "bars_held": current_bar_index - self.last_trade_bar})

            return ctx.signal("hold") # Hold if no exit condition met

        # Cooldown check
        if current_bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: Only at the start of the US session (13:00 UTC bar)
        if is_us_session_start_bar and self.prior_us_session_close is not None:
            self.last_us_session_start_bar = current_bar_index # Mark this bar as session start processed
            
            session_open = ctx.bar.open
            gap_pct = (session_open - self.prior_us_session_close) / self.prior_us_session_close

            # Check for significant gap
            if abs(gap_pct) > self.gap_threshold_pct:
                # Check for lack of continuation in the first bar of the session
                # We need the current bar's close to determine if it continued the gap
                continuation_pct = (ctx.bar.close - session_open) / session_open

                # Calculate current volatility for confidence scaling
                stddev = self._stddev(closes, self.ema_period)
                volatility_factor = 1.0
                if stddev:
                    # Scale confidence inversely with recent volatility
                    # If stddev is 0.01 (1%), volatility_factor = 1.0 / 0.01 = 100 -- this isn't right
                    # A better way is to use a normalized volatility (e.g. daily range / ATR)
                    # For simplicity, let's just make it a cap
                    volatility_factor = min(1.0, 0.005 / (stddev / ctx.bar.close)) if stddev > 0 else 1.0 # Assuming stddev is price, normalize to %

                # Long signal: Significant down gap, but price recovers slightly or doesn't continue down
                if gap_pct < -self.gap_threshold_pct: # Down gap
                    if continuation_pct > -self.continuation_threshold_pct: # Not continuing down significantly
                        self.last_trade_bar = current_bar_index
                        confidence = 0.6 + ctx.rng.uniform(-0.1, 0.1) * volatility_factor
                        return ctx.signal("long", confidence=confidence,
                                          metadata={
                                              "reason": "us_session_down_gap_reversion",
                                              "prior_us_close": self.prior_us_session_close,
                                              "session_open": session_open,
                                              "gap_pct": gap_pct,
                                              "continuation_pct": continuation_pct,
                                              "current_close": ctx.bar.close,
                                              "stddev": stddev
                                          })

                # Short signal: Significant up gap, but price pulls back slightly or doesn't continue up
                elif gap_pct > self.gap_threshold_pct: # Up gap
                    if continuation_pct < self.continuation_threshold_pct: # Not continuing up significantly
                        self.last_trade_bar = current_bar_index
                        confidence = 0.6 + ctx.rng.uniform(-0.1, 0.1) * volatility_factor
                        return ctx.signal("short", confidence=confidence,
                                          metadata={
                                              "reason": "us_session_up_gap_reversion",
                                              "prior_us_close": self.prior_us_session_close,
                                              "session_open": session_open,
                                              "gap_pct": gap_pct,
                                              "continuation_pct": continuation_pct,
                                              "current_close": ctx.bar.close,
                                              "stddev": stddev
                                          })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar on close to allow new trades after cooldown
        self.last_trade_bar = ctx.bar_index