from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolAsiaGapReversion(Strategy):
    METADATA = {
        "name": "SOL Asia Gap Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 180.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold is up to 4 hours, or end of session
        "warmup_bars": 50,
        "required_features": [], # No specific features required beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_asia_session_close = None
        self.last_session_day = None
        self.traded_today = False
        self.cooldown_bars = 0
        self.cooldown_period = 6 # Wait 6 bars (6 hours) after a trade before new entry

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Update cooldown
        if self.cooldown_bars > 0:
            self.cooldown_bars -= 1
            return None

        # --- Session Management ---
        # Asia session: UTC 0-8 (8 hours)
        # We need to detect the start of a new Asia session and its prior close.
        # Let's consider 0-8 as Asia session.
        # The close of the *previous* day's Asia session (UTC 7th hour) is what we care about for the gap.

        # If it's a new day, reset traded_today flag
        if current_day != self.last_session_day:
            self.traded_today = False
            self.last_session_day = current_day

        # At the end of the Asia session (e.g., UTC 7th hour, before 8:00), record the close
        if current_hour == 7: # The last bar of the Asia session
            self.last_asia_session_close = ctx.bar.close

        # Check for gap at the start of a new Asia session (UTC 0 hour)
        # We need the close from the *previous* day's Asia session.
        if current_hour == 0 and self.last_asia_session_close is not None and not ctx.has_position() and not self.traded_today:
            # The current bar's open is the open of the new Asia session
            gap = ctx.bar.open - self.last_asia_session_close
            gap_percent = (gap / self.last_asia_session_close) * 100

            # Define gap thresholds (e.g., 0.5% gap)
            gap_threshold_pct = 0.4 # 0.4% gap for a signal

            # Check for continuation (e.g., first few bars of session move further in gap direction)
            # We'll simplify this by just looking at the initial gap and assuming if it opens wide
            # and doesn't immediately reverse, it's a gap that could revert.

            # We want to trade back towards the prior close if the gap is large and lacks immediate continuation.
            # For simplicity, if the gap is significant, we assume it might revert.
            # A positive gap means price opened higher, we look for a short.
            # A negative gap means price opened lower, we look for a long.

            # Short signal: large positive gap
            if gap_percent > gap_threshold_pct:
                # Check for mild initial continuation or just the gap itself.
                # If price is still above the prior close, it's a candidate for reversion
                if ctx.bar.close > self.last_asia_session_close:
                    self.traded_today = True
                    self.cooldown_bars = self.cooldown_period
                    return ctx.signal(
                        "short",
                        confidence=0.65,
                        metadata={
                            "reason": "asia_session_gap_reversion_short",
                            "gap_percent": gap_percent,
                            "prior_asia_close": self.last_asia_session_close,
                            "current_open": ctx.bar.open,
                            "current_close": ctx.bar.close,
                        }
                    )
            # Long signal: large negative gap
            elif gap_percent < -gap_threshold_pct:
                # If price is still below the prior close, it's a candidate for reversion
                if ctx.bar.close < self.last_asia_session_close:
                    self.traded_today = True
                    self.cooldown_bars = self.cooldown_period
                    return ctx.signal(
                        "long",
                        confidence=0.65,
                        metadata={
                            "reason": "asia_session_gap_reversion_long",
                            "gap_percent": gap_percent,
                            "prior_asia_close": self.last_asia_session_close,
                            "current_open": ctx.bar.open,
                            "current_close": ctx.bar.close,
                        }
                    )

        # --- Position Management ---
        # If we have a position and it's approaching the end of the Asia session, flatten it.
        # Or if we've held for too long (e.g., 4 hours as per declared_hold_seconds)
        if ctx.has_position():
            # Exit before UTC 8:00 (i.e., at UTC 7:00 bar close)
            # This ensures we don't hold into the next major session.
            if current_hour == 7:
                return ctx.signal(
                    "flat",
                    confidence=0.8, # High confidence to exit before session end
                    metadata={
                        "reason": "session_end_flatten",
                        "current_hour": current_hour,
                        "entry_direction": ctx.position_direction(),
                        "entry_price": ctx.position["entry_price"]
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown_bars here to ensure it's applied after any close,
        # whether by TP/SL or explicit flat signal.
        self.cooldown_bars = self.cooldown_period
        pass # No adaptive logic for now, just for cooldown reset.