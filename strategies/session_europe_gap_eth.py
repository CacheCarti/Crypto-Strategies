from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEuropeGapReversion(Strategy):
    METADATA = {
        "name": "EthEuropeGapReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold for 1h bars, could be shorter if gap closes quickly
        "warmup_bars": 24, # Need enough bars for prior session close and some price action
        "required_features": [], # No external features needed for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12 # Wait 12 bars (12 hours) after a trade before new entry
        self.last_trade_bar_index = -self.cooldown_bars
        self.prior_session_close = None
        self.last_session_day = -1 # Track day to ensure only one trade per session
        self.entry_price = 0.0

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        opens = ctx.opens(self.METADATA["warmup_bars"] + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # --- Position Management ---
        if ctx.has_position():
            # Exit by end of European session (UTC 12) if still open
            if current_hour >= 12 and current_day == self.last_session_day:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "time_based_exit_european_session_end",
                        "current_hour": current_hour,
                        "entry_price": self.entry_price,
                        "current_price": ctx.bar.close,
                    },
                )
            # Or if price crosses back significantly below/above prior close, exit (structure stop)
            if ctx.position_direction() == "long":
                if self.prior_session_close and ctx.bar.close < self.prior_session_close * 0.995: # 0.5% below prior close
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "structure_stop_long_gap_closed_below_prior_close",
                            "prior_session_close": self.prior_session_close,
                            "current_price": ctx.bar.close,
                        },
                    )
            elif ctx.position_direction() == "short":
                if self.prior_session_close and ctx.bar.close > self.prior_session_close * 1.005: # 0.5% above prior close
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "structure_stop_short_gap_closed_above_prior_close",
                            "prior_session_close": self.prior_session_close,
                            "current_price": ctx.bar.close,
                        },
                    )
            return None # Hold position

        # --- Cooldown Check ---
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # --- European Session Entry Logic (UTC 7-12) ---
        # Find the close of the previous European session (UTC 7-12)
        # We need to ensure we only capture one prior session close per day
        # and that it's from a completed session.
        if current_hour == 7 and current_day != self.last_session_day:
            # At 7 UTC, the previous day's 12 UTC close is 19 hours ago
            # If we don't have enough history, skip
            if len(closes) < 20: # 19 bars back for the close + current bar
                return None
            
            # The close 19 bars ago would be the 12 UTC close of the previous day
            self.prior_session_close = closes[-20] # close at T-19 (12 UTC of previous day)
            self.last_session_day = current_day # Mark current day to prevent re-evaluating prior session close

        # Entry condition: between UTC 8 and 11 (inclusive), if a gap exists and is not continuing
        if 8 <= current_hour <= 11 and self.prior_session_close is not None:
            current_open = ctx.bar.open
            current_close = ctx.bar.close

            gap_threshold_bps = 50 # 0.5% gap
            
            # Check for a significant gap down from prior session close
            if current_open < self.prior_session_close * (1 - gap_threshold_bps / 10000.0):
                # Check for lack of continuation and reversal towards prior close
                # E.g., if it opened down but is now closing higher, or close is above open
                if current_close > current_open and current_close > self.prior_session_close * (1 - gap_threshold_bps / 20000.0): # Closing halfway or more
                    self.last_trade_bar_index = ctx.bar_index
                    self.entry_price = current_close
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "europe_session_gap_down_reversion_long",
                            "prior_session_close": self.prior_session_close,
                            "current_open": current_open,
                            "current_close": current_close,
                            "gap_bps": (self.prior_session_close - current_open) / self.prior_session_close * 10000,
                            "current_hour": current_hour,
                        },
                    )

            # Check for a significant gap up from prior session close
            elif current_open > self.prior_session_close * (1 + gap_threshold_bps / 10000.0):
                # Check for lack of continuation and reversal towards prior close
                # E.g., if it opened up but is now closing lower, or close is below open
                if current_close < current_open and current_close < self.prior_session_close * (1 + gap_threshold_bps / 20000.0): # Closing halfway or more
                    self.last_trade_bar_index = ctx.bar_index
                    self.entry_price = current_close
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "europe_session_gap_up_reversion_short",
                            "prior_session_close": self.prior_session_close,
                            "current_open": current_open,
                            "current_close": current_close,
                            "gap_bps": (current_open - self.prior_session_close) / self.prior_session_close * 10000,
                            "current_hour": current_hour,
                        },
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset prior session close if it was based on a different day
        # This ensures we re-evaluate for the next day's session
        pass # No specific action needed here, self.last_trade_bar_index is updated on entry