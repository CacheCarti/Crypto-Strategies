from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEuropeGapReversion(Strategy):
    METADATA = {
        "name": "SOL Europe Gap Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 18000, # Max hold until UTC 12, or earlier by stop
        "warmup_bars": 24, # Need at least 24 bars for prior day close
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar_index = -100 # Cooldown
        self.prior_day_close = None
        self.traded_today = False
        self.entry_bar_index = -1

        # Parameters
        self.gap_threshold_pct = 0.005 # 0.5% gap
        self.continuation_threshold_pct = 0.003 # 0.3% continuation
        self.cooldown_bars = 6
        self.entry_hour_start = 7
        self.entry_hour_end = 10 # Enter up to 10:00 UTC
        self.exit_hour = 12 # Exit by 12:00 UTC

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(25)
        opens = ctx.opens(25)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Daily reset logic
        if current_hour == 0 and not self.traded_today:
            # Store prior day's close at the start of a new day (UTC 00:00)
            # This handles cases where we might miss the last bar of the previous day
            self.prior_day_close = closes[-2] # Close of the previous day's last bar
            self.traded_today = False
            
        # Check if we are in the entry window and haven't traded yet today
        if self.entry_hour_start <= current_hour < self.entry_hour_end and not self.traded_today:
            # Ensure we have a valid prior day close and enough data for gap calculation
            if self.prior_day_close is None:
                return None

            current_open = ctx.bar.open
            gap_pct = (current_open - self.prior_day_close) / self.prior_day_close

            # Check for a large gap
            if abs(gap_pct) > self.gap_threshold_pct:
                # Check for lack of continuation after the open
                # We need at least two bars (current bar and the one after the gap open)
                if len(closes) < 2:
                    return None

                # Price movement since open (current bar's close vs open)
                continuation = (ctx.bar.close - current_open) / current_open

                # Long condition: Gap down, but no strong downward continuation
                if gap_pct < -self.gap_threshold_pct and continuation > -self.continuation_threshold_pct:
                    if not ctx.has_position() and (ctx.bar_index - self.last_trade_bar_index) > self.cooldown_bars:
                        self.traded_today = True
                        self.entry_bar_index = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            metadata={
                                "reason": "europe_gap_down_reversion",
                                "prior_day_close": self.prior_day_close,
                                "current_open": current_open,
                                "gap_pct": gap_pct,
                                "continuation_pct": continuation,
                                "hour": current_hour
                            }
                        )

                # Short condition: Gap up, but no strong upward continuation
                elif gap_pct > self.gap_threshold_pct and continuation < self.continuation_threshold_pct:
                    if not ctx.has_position() and (ctx.bar_index - self.last_trade_bar_index) > self.cooldown_bars:
                        self.traded_today = True
                        self.entry_bar_index = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            metadata={
                                "reason": "europe_gap_up_reversion",
                                "prior_day_close": self.prior_day_close,
                                "current_open": current_open,
                                "gap_pct": gap_pct,
                                "continuation_pct": continuation,
                                "hour": current_hour
                            }
                        )

        # Exit condition: Time-based exit by end of session
        if ctx.has_position() and current_hour >= self.exit_hour and (ctx.bar_index > self.entry_bar_index):
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": "time_based_session_exit",
                    "exit_hour": self.exit_hour,
                    "position_direction": ctx.position_direction(),
                    "bars_held": ctx.bar_index - self.entry_bar_index
                }
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar_index = ctx.bar_index
        # Reset traded_today if it was a very short position that closed before the end of the entry window
        # This prevents locking out trades if a quick stop-out occurs early in the session
        if ctx.bar.timestamp.hour < self.entry_hour_end:
            self.traded_today = False