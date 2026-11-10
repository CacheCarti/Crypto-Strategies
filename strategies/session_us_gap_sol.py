from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolUsSessionGapReversion(Strategy):
    METADATA = {
        "name": "SOL US Session Gap Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intend to hold for up to 4 hours within the session
        "warmup_bars": 30,
        "required_features": [], # No specific features required for this price-action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13  # UTC
        self.us_session_end_hour = 21    # UTC (exclusive, so exit before 21:00)
        self.cooldown_bars = 6           # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade if conditions met

        # Store prior session's closing price and next session's opening price
        self.prior_session_close = None
        self.current_session_open = None
        self.session_has_traded = False # To ensure only one entry per session

        # Gap threshold in percentage (e.g., 0.5% gap)
        self.gap_threshold_pct = 0.005 # 0.5%
        # Continuation threshold (e.g., 0.2% continuation in the gap direction)
        self.continuation_threshold_pct = 0.002 # 0.2%

        # Indicator periods
        self.ema_period_fast = 9
        self.ema_period_slow = 21

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)

        if len(closes) < self.warmup_bars or len(opens) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # --- Session Management ---
        # Reset at the start of a new US session
        if current_hour == self.us_session_start_hour and current_bar_index != self.last_trade_bar:
            if not self.session_has_traded: # Only update if we didn't trade in the last session
                self.prior_session_close = closes[-2] # Close of the previous bar (end of prior session)
                self.current_session_open = opens[-1] # Open of the current bar (start of US session)
                self.session_has_traded = False # Reset for the new session
            else:
                # If we traded in the last session, just reset the flag
                self.session_has_traded = False

        # If a position is open, check for session end or stop/profit
        if ctx.has_position():
            # Exit if nearing the end of the US session
            if current_hour >= self.us_session_end_hour - 1: # Exit one hour before end to be safe
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_imminent", "current_hour": current_hour})
            return None # Hold position

        # --- Cooldown Logic ---
        if current_bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # --- Entry Logic (only within US session) ---
        if self.us_session_start_hour <= current_hour < self.us_session_end_hour and not self.session_has_traded:
            if self.prior_session_close is None or self.current_session_open is None:
                return None

            gap_pct = (self.current_session_open - self.prior_session_close) / self.prior_session_close

            # Calculate EMAs for trend filter
            ema_fast = self._ema(closes, self.ema_period_fast)
            ema_slow = self._ema(closes, self.ema_period_slow)

            if ema_fast is None or ema_slow is None:
                return None

            # Bullish Gap Down Reversion
            if gap_pct < -self.gap_threshold_pct: # Significant gap down
                # Check for lack of continuation down (price moves back up)
                # Current close is above current session open, indicating reversal
                if closes[-1] > self.current_session_open * (1 + self.continuation_threshold_pct):
                    # And price is above EMAs (or EMAs are bullishly crossed)
                    if ema_fast > ema_slow and closes[-1] > ema_fast:
                        self.last_trade_bar = current_bar_index
                        self.session_has_traded = True
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "session_gap_down_reversion",
                                "gap_pct": gap_pct,
                                "prior_close": self.prior_session_close,
                                "session_open": self.current_session_open,
                                "current_close": closes[-1],
                                "ema_fast": ema_fast,
                                "ema_slow": ema_slow,
                            },
                        )

            # Bearish Gap Up Reversion
            elif gap_pct > self.gap_threshold_pct: # Significant gap up
                # Check for lack of continuation up (price moves back down)
                # Current close is below current session open, indicating reversal
                if closes[-1] < self.current_session_open * (1 - self.continuation_threshold_pct):
                    # And price is below EMAs (or EMAs are bearishly crossed)
                    if ema_fast < ema_slow and closes[-1] < ema_fast:
                        self.last_trade_bar = current_bar_index
                        self.session_has_traded = True
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            metadata={
                                "reason": "session_gap_up_reversion",
                                "gap_pct": gap_pct,
                                "prior_close": self.prior_session_close,
                                "session_open": self.current_session_open,
                                "current_close": closes[-1],
                                "ema_fast": ema_fast,
                                "ema_slow": ema_slow,
                            },
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset the trade flag if the position closed for any reason
        self.session_has_traded = False
        # Log the close for debugging/analysis
        ctx.log(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps, held {position['bars_held']} bars. Reason: {position.get('exit_reason', 'unknown')}")