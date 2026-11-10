from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionOvernightGapETH(Strategy):
    METADATA = {
        "name": "Session Overnight Gap ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Roughly 3 hours
        "warmup_bars": 40,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_window_start_hour = 0  # UTC 0
        self.overnight_window_end_hour = 6    # UTC 6
        self.us_session_start_hour = 13       # UTC 13 (9 AM ET)

        self.last_signal_bar_index = -100 # Cooldown to avoid overtrading
        self.cooldown_bars = 12 # Wait 12 hours after a signal before trading again (12 bars * 1h = 12 hours)

        # Store last known close of the prior session for gap calculation
        self.prior_session_close = None
        self.prior_session_open = None
        self.prior_session_high = None
        self.prior_session_low = None
        self.is_overnight_session = False
        self.overnight_session_started_bar_index = -1

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # Logic to detect start of overnight session and store prior session's close
        if not self.is_overnight_session and current_hour == self.overnight_window_start_hour:
            # Entering the overnight session. The current bar's open is the open of the overnight session.
            # The close of the previous bar (closes[-2]) is the close of the prior trading session.
            if len(closes) >= 2 and len(opens) >= 2 and len(highs) >= 2 and len(lows) >= 2:
                self.prior_session_close = closes[-2] # Close of the day before overnight session
                self.prior_session_open = opens[-2]
                self.prior_session_high = highs[-2]
                self.prior_session_low = lows[-2]
                self.is_overnight_session = True
                self.overnight_session_started_bar_index = current_bar_index
                # ctx.logger.info(f"Overnight session started at {ctx.bar.timestamp}. Prior session close: {self.prior_session_close}")

        # Reset overnight session flag when exiting the window
        if self.is_overnight_session and current_hour == self.overnight_window_end_hour:
            self.is_overnight_session = False
            self.prior_session_close = None
            self.prior_session_open = None
            self.prior_session_high = None
            self.prior_session_low = None
            # ctx.logger.info(f"Overnight session ended at {ctx.bar.timestamp}.")

        # Check for trade exit conditions before the US session starts
        if ctx.has_position() and current_hour == self.us_session_start_hour - 1: # Exit one hour before US session
            position_direction = ctx.position_direction()
            exit_reason = f"Exiting {position_direction} before US session opens"
            # ctx.logger.info(f"Preparing to exit position: {exit_reason}")
            return ctx.signal("flat", confidence=0.8, metadata={"reason": exit_reason, "current_price": ctx.bar.close})

        # Trading logic only within the overnight window and if a prior session close is available
        if self.is_overnight_session and self.prior_session_close is not None:
            # Ensure we only consider a gap from the very beginning of the overnight session
            if current_bar_index == self.overnight_session_started_bar_index:
                # Calculate the gap relative to the prior session's close
                gap_pct = ((ctx.bar.open - self.prior_session_close) / self.prior_session_close) * 100

                # Define gap thresholds in percentage
                min_gap_long = 0.5 # 0.5% gap down
                min_gap_short = 0.5 # 0.5% gap up

                # Cooldown check
                if current_bar_index - self.last_signal_bar_index < self.cooldown_bars:
                    # ctx.logger.info(f"Cooldown active. Skipping signal at {ctx.bar.timestamp}")
                    return None

                # Entry conditions:
                # 1. Significant gap down at overnight open
                # 2. Price fails to continue lower (e.g., current close is above open, or above an immediate EMA)
                # 3. Trade back towards prior session close
                if gap_pct < -min_gap_long: # Gap down
                    # Look for signs of reversal/lack of continuation
                    # Simple check: current bar's close is higher than its open, or current price is recovering
                    if ctx.bar.close > ctx.bar.open and ctx.bar.close > (ctx.bar.open + self.prior_session_close) / 2: # price recovering
                        self.last_signal_bar_index = current_bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "overnight_gap_down_reversal",
                                "gap_pct": round(gap_pct, 2),
                                "prior_session_close": round(self.prior_session_close, 2),
                                "overnight_open": round(ctx.bar.open, 2),
                                "current_close": round(ctx.bar.close, 2),
                                "regime": ctx.market.get("regime", "NORMAL"),
                            }
                        )
                elif gap_pct > min_gap_short: # Gap up
                    # Look for signs of reversal/lack of continuation
                    # Simple check: current bar's close is lower than its open, or current price is falling
                    if ctx.bar.close < ctx.bar.open and ctx.bar.close < (ctx.bar.open + self.prior_session_close) / 2: # price falling
                        self.last_signal_bar_index = current_bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "overnight_gap_up_reversal",
                                "gap_pct": round(gap_pct, 2),
                                "prior_session_close": round(self.prior_session_close, 2),
                                "overnight_open": round(ctx.bar.open, 2),
                                "current_close": round(ctx.bar.close, 2),
                                "regime": ctx.market.get("regime", "NORMAL"),
                            }
                        )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # ctx.logger.info(f"Position closed: {position}")
        # Reset cooldown after a position closes, allowing for a new trade sooner if conditions are met
        # Or you could make it longer to avoid revenge trading. For now, we'll let the on_bar cooldown handle it.
        pass