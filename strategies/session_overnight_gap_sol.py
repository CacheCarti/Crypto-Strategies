from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightGapReversionSOL(Strategy):
    METADATA = {
        "name": "Overnight Gap Reversion SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 300.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hour holds
        "warmup_bars": 24, # Need enough bars for 2 full session closes
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -1
        self.cooldown_bars = 6 # Wait 6 hours after a trade
        self.prior_session_close = None
        self.session_gap_bar_index = -1

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars and ctx.has_position():
            return None # If we're in a position and still in cooldown, hold

        # Logic to identify session close (UTC 0-6 window)
        # We define the "overnight session" as UTC 0-6.
        # The prior session close is the close of the last bar before UTC 0.
        # We need at least one full day's worth of bars to establish context.

        # If current hour is 0 (start of overnight session), capture the prior day's close
        # This means the close of the bar at 23:00 UTC (index -2 if current is 0:00)
        # For simplicity, let's say the 'prior session close' is the close of the 23:00 UTC bar.
        if current_hour == 0 and self.session_gap_bar_index != ctx.bar_index -1:
            # Check if we have enough history to get the 23:00 UTC close
            # We need at least 2 bars to get current and previous
            if len(closes) >= 2:
                # The close of the bar ending at 23:00 UTC (previous bar)
                self.prior_session_close = closes[-2] # This is the close of the 23:00 UTC bar
                self.session_gap_bar_index = ctx.bar_index - 1 # Mark the bar where we set the prior close
                # print(f"DEBUG: Bar {ctx.bar_index}, Hour {current_hour}. Set prior_session_close: {self.prior_session_close}")


        # Trading logic within the overnight session (UTC 0-6)
        if self.prior_session_close is not None and 0 <= current_hour < 6:
            # Only enter if no position is open
            if not ctx.has_position():
                # Check for a gap and lack of continuation
                # We consider the 'open' of the current 0-6 UTC session as the opening price
                # For simplicity, let's use the current bar's open price as the session open
                session_open = ctx.bar.open
                current_price = ctx.bar.close
                
                # Calculate gap percentage
                gap_pct = ((session_open - self.prior_session_close) / self.prior_session_close) * 100

                # Simple Moving Average for trend confirmation
                sma_period = 20
                sma = self._sma(closes, sma_period)
                if sma is None:
                    return None

                # Long signal: Gap down, but price is trying to revert upwards (above open or near prior close)
                # And the overall trend is not strongly bearish (price not too far below SMA)
                if gap_pct < -0.2: # Significant gap down (e.g., 0.2% or more)
                    if current_price > session_open and current_price < self.prior_session_close:
                        # Price gapped down, but current price is above session open and below prior session close,
                        # implying a move back towards the prior close.
                        # Also, ensure we are not in a strong downtrend by checking against SMA
                        if current_price > sma * 0.99: # Not too far below SMA
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.7,
                                metadata={
                                    "reason": "overnight_gap_down_reversion",
                                    "prior_session_close": self.prior_session_close,
                                    "session_open": session_open,
                                    "current_price": current_price,
                                    "gap_pct": gap_pct,
                                    "sma_20": sma
                                }
                            )

                # Short signal: Gap up, but price is trying to revert downwards (below open or near prior close)
                # And the overall trend is not strongly bullish (price not too far above SMA)
                elif gap_pct > 0.2: # Significant gap up
                    if current_price < session_open and current_price > self.prior_session_close:
                        # Price gapped up, but current price is below session open and above prior session close,
                        # implying a move back towards the prior close.
                        # Also, ensure we are not in a strong uptrend by checking against SMA
                        if current_price < sma * 1.01: # Not too far above SMA
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.7,
                                metadata={
                                    "reason": "overnight_gap_up_reversion",
                                    "prior_session_close": self.prior_session_close,
                                    "session_open": session_open,
                                    "current_price": current_price,
                                    "gap_pct": gap_pct,
                                    "sma_20": sma
                                }
                            )
        
        # Exit logic: If we are in a position and it's approaching the end of the overnight session (UTC 6)
        # or if the current bar is the one that sets a new `prior_session_close` (meaning a new day started)
        if ctx.has_position():
            # Exit before the next major session starts (e.g., 6:00 UTC)
            if current_hour >= 6:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "session_end_exit",
                        "current_hour": current_hour,
                        "prior_session_close": self.prior_session_close,
                        "current_price": ctx.bar.close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any session-specific state if needed, or log for adaptive sizing.
        # For this strategy, the `last_trade_bar` is enough for cooldown.
        pass