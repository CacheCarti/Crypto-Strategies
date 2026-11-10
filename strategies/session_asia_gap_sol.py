from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionGapReversionSOL(Strategy):
    METADATA = {
        "name": "Asia Session Gap Reversion SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2, # Up to 2 hours, aiming for session end
        "warmup_bars": 50, # Need enough bars for 2 previous sessions
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar_index = -self.METADATA["warmup_bars"] # Allow trading immediately after warmup
        self.cooldown_bars = 6 # Wait 6 hours after a trade
        self.asia_session_start_hour = 0 # UTC 00:00
        self.asia_session_end_hour = 8 # UTC 08:00
        self.max_gap_lookback_bars = 8 # Look back up to 8 bars (8 hours) for prior session close

        # Store session data
        self.prior_asia_close_price = None
        self.prior_asia_close_bar_index = -1
        self.current_asia_session_started_bar_index = -1
        self.traded_this_session = False

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None # Not enough data for warmup

        current_hour = ctx.bar.timestamp.hour

        # --- Session Management ---
        # Detect start of Asia session
        if current_hour == self.asia_session_start_hour and self.current_asia_session_started_bar_index != ctx.bar_index:
            # New Asia session started
            self.current_asia_session_started_bar_index = ctx.bar_index
            self.traded_this_session = False

            # Find the close of the PREVIOUS Asia session
            # This is the bar before the current Asia session started
            # Look back `max_gap_lookback_bars` to find the last bar of the previous session (hour 23 UTC)
            prior_session_close_idx = -1
            for i in range(1, min(ctx.bar_index, self.max_gap_lookback_bars) + 1):
                # Check if the previous bar (ctx.bar_index - i) was the end of the prior session (hour 23)
                # And if the bar before that was within the previous session (e.g., hour 22)
                # This ensures we capture the *last* bar of the previous day's Asia session
                # If we're at 00:00 UTC, the prior session ended at 23:00 UTC on the previous day.
                # We need to find the close of that 23:00 bar.
                if ctx.bar_index - i >= 0:
                    prev_bar_timestamp = ctx.bar.timestamp - (i * (ctx.bar.timestamp - (ctx.bar.timestamp.replace(hour=ctx.bar.timestamp.hour - 1)))) # Approximate prev bar timestamp
                    # This check is tricky. A simpler way is to look back to the bar before the current Asia session started.
                    # The bar at `ctx.bar_index - 1` is the 23:00 UTC bar of the previous day.
                    # The close of this bar is `closes[-1-i]`.
                    if i == 1: # The immediately preceding bar
                         self.prior_asia_close_price = closes[-2] if len(closes) >= 2 else None # The close of the 23:00 UTC bar of the previous day
                         self.prior_asia_close_bar_index = ctx.bar_index - 1
                         break # Found the prior session close

        # --- Exit Logic (before session end) ---
        if ctx.has_position():
            # If we are in a position and it's near the end of the Asia session, consider exiting
            if current_hour >= self.asia_session_end_hour - 1 and ctx.bar.timestamp.minute >= 50: # 10 mins before session end
                if ctx.position_direction() == "long":
                    reason = "exit_before_asia_close_long"
                else:
                    reason = "exit_before_asia_close_short"
                return ctx.signal("flat", confidence=0.8, metadata={"reason": reason, "current_price": ctx.bar.close})

        # --- Entry Logic ---
        if ctx.has_position() or \
           ctx.bar_index < self.last_trade_bar_index + self.cooldown_bars or \
           self.traded_this_session or \
           self.prior_asia_close_price is None or \
           current_hour < self.asia_session_start_hour or \
           current_hour >= self.asia_session_end_hour:
            return None # Already in position, cooldown, already traded this session, no prior close, or outside Asia session

        current_open = ctx.bar.open
        gap_magnitude_bps = ((current_open - self.prior_asia_close_price) / self.prior_asia_close_price) * 10000

        # Define thresholds for a "large" gap
        min_gap_bps = 50 # 0.5% gap
        continuation_threshold_bps = 10 # 0.1% move in the same direction

        # Check for gap and lack of continuation
        if abs(gap_magnitude_bps) > min_gap_bps:
            # Check the current bar's movement relative to the open
            # If it's a gap up and the current bar is moving down (or not continuing up strongly)
            if gap_magnitude_bps > min_gap_bps and (ctx.bar.close < current_open or (ctx.bar.close - current_open) / current_open * 10000 < continuation_threshold_bps):
                # Gap up, not continuing up, so short to revert to prior close
                self.traded_this_session = True
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.asia_session_end_hour - current_hour,
                    metadata={
                        "reason": "asia_gap_up_reversion",
                        "gap_bps": round(gap_magnitude_bps, 2),
                        "prior_asia_close": round(self.prior_asia_close_price, 2),
                        "current_open": round(current_open, 2),
                        "current_close": round(ctx.bar.close, 2),
                        "session_start_hour": self.asia_session_start_hour,
                        "current_hour": current_hour
                    }
                )
            # If it's a gap down and the current bar is moving up (or not continuing down strongly)
            elif gap_magnitude_bps < -min_gap_bps and (ctx.bar.close > current_open or (current_open - ctx.bar.close) / current_open * 10000 < continuation_threshold_bps):
                # Gap down, not continuing down, so long to revert to prior close
                self.traded_this_session = True
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.asia_session_end_hour - current_hour,
                    metadata={
                        "reason": "asia_gap_down_reversion",
                        "gap_bps": round(gap_magnitude_bps, 2),
                        "prior_asia_close": round(self.prior_asia_close_price, 2),
                        "current_open": round(current_open, 2),
                        "current_close": round(ctx.bar.close, 2),
                        "session_start_hour": self.asia_session_start_hour,
                        "current_hour": current_hour
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details for analysis
        # print(f"Position closed: {position['direction']} at {position['exit_price']} (entry: {position['entry_price']}) after {position['bars_held']} bars. Return: {position['return_bps']} bps.")
        pass