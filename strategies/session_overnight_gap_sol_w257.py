from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolOvernightGapReversion(Strategy):
    METADATA = {
        "name": "SOL Overnight Gap Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hours hold
        "warmup_bars": 24, # Need enough bars for prior day's close
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_signal_bar_index = -1
        self.cooldown_bars = 6 # Wait 6 hours (6 bars) after a trade
        self.prior_session_close = None
        self.session_gap_threshold_bps = 50 # 0.5% gap to consider
        self.reversion_target_multiplier = 0.5 # Aim to revert 50% of the gap
        self.session_start_hour = 0 # UTC 00:00
        self.session_end_hour = 6 # UTC 06:00
        self.trading_window_end_hour = 12 # Stop trading by UTC 12:00
        self.daily_trade_done = False # To ensure only one trade per session structure

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset daily trade flag and capture prior session close at the start of a new session
        if current_hour == self.session_start_hour and not self.daily_trade_done:
            # Look for the close of the previous day's session (e.g., yesterday at 23:00 UTC)
            # This is a bit tricky with 1h bars. We assume the 'close' at 23:00 UTC on the previous day
            # represents the end of the prior session before the gap.
            # We need at least 24 bars to get to yesterday's close.
            if len(closes) >= 24:
                self.prior_session_close = closes[-24] # Close from 24 hours ago
            else:
                self.prior_session_close = None
            self.daily_trade_done = False # Reset for the new day
            return None # Don't trade on the first bar of the new session, just capture prior close

        # If a position is open, let the engine manage it or hold
        if ctx.has_position():
            # If we are approaching the end of our trading window, consider exiting
            if current_hour >= self.trading_window_end_hour - 1: # Exit before the last hour of the window
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None

        # Cooldown period check
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # Entry logic: Only trade within the overnight session and if we haven't traded today
        if self.session_start_hour <= current_hour < self.session_end_hour and not self.daily_trade_done and self.prior_session_close is not None:
            current_open = ctx.bar.open
            gap_bps = ((current_open - self.prior_session_close) / self.prior_session_close) * 10000

            # Check for a significant gap
            if abs(gap_bps) > self.session_gap_threshold_bps:
                # Calculate target for reversion
                reversion_target_price = self.prior_session_close + (self.prior_session_close * gap_bps / 10000 * (1 - self.reversion_target_multiplier))

                if gap_bps > 0: # Gap up, look for short reversion
                    # Enter short if current price is still above the prior close and shows signs of weakness (e.g., current close is lower than open)
                    if ctx.bar.close < ctx.bar.open: # Simple bearish candle after gap up
                        self.last_signal_bar_index = ctx.bar_index
                        self.daily_trade_done = True
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            take_profit_bps=abs(int(gap_bps * self.reversion_target_multiplier)),
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "overnight_gap_up_reversion_short",
                                "gap_bps": round(gap_bps, 2),
                                "prior_session_close": round(self.prior_session_close, 2),
                                "current_open": round(current_open, 2),
                                "current_close": round(ctx.bar.close, 2),
                                "reversion_target_price": round(reversion_target_price, 2)
                            }
                        )
                elif gap_bps < 0: # Gap down, look for long reversion
                    # Enter long if current price is still below the prior close and shows signs of strength (e.g., current close is higher than open)
                    if ctx.bar.close > ctx.bar.open: # Simple bullish candle after gap down
                        self.last_signal_bar_index = ctx.bar_index
                        self.daily_trade_done = True
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            take_profit_bps=abs(int(gap_bps * self.reversion_target_multiplier)),
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "overnight_gap_down_reversion_long",
                                "gap_bps": round(gap_bps, 2),
                                "prior_session_close": round(self.prior_session_close, 2),
                                "current_open": round(current_open, 2),
                                "current_close": round(ctx.bar.close, 2),
                                "reversion_target_price": round(reversion_target_price, 2)
                            }
                        )
        
        # Reset daily_trade_done flag at the end of the trading window to allow trading on the next day
        if current_hour == self.trading_window_end_hour:
            self.daily_trade_done = False
            self.prior_session_close = None # Clear prior close for next day's calculation

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust parameters if needed
        pass