from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightGapReversion(Strategy):
    METADATA = {
        "name": "Overnight Gap Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 24, # Need enough bars for 24-hour lookback
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_close = None
        self.last_trade_bar_index = -self.METADATA["warmup_bars"] - 1 # Initialize to allow first trade
        self.cooldown_bars = 6 # Wait 6 hours after a trade

        # Define session boundaries (UTC hours)
        self.overnight_session_start = 0
        self.overnight_session_end = 6 # 00:00 to 06:00 UTC
        self.us_session_start = 13 # 13:00 UTC (9 AM EST)

        # Gap threshold in percentage
        self.gap_threshold_pct = 0.5 # 0.5% gap
        self.continuation_threshold_pct = 0.2 # 0.2% continuation beyond gap

        # Volatility filter: only trade if recent volatility is not too low
        self.volatility_period = 12
        self.min_volatility_multiplier = 0.0005 # 0.05% ATR

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for cooldown
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Logic to capture the overnight close
        if current_hour == self.overnight_session_end: # At the end of the overnight session
            self.overnight_close = ctx.bar.close
            # Reset the last trade index to allow trading in the next session if conditions met
            self.last_trade_bar_index = -self.METADATA["warmup_bars"] - 1 # Allow trading next session
            return None

        # Main trading logic: after overnight session, before US session
        if self.overnight_close is not None and \
           current_hour > self.overnight_session_end and \
           current_hour < self.us_session_start and \
           not ctx.has_position():

            # Only consider the first bar after the overnight session for gap detection
            # Or, more robustly, consider the first open after the overnight_session_end bar
            # We assume ctx.bar.open is the open of the current bar
            # The gap is between self.overnight_close and ctx.bar.open
            
            # To ensure we only check for a gap once, we can check if the overnight_close
            # was set on the previous bar and if the current bar is the first one after.
            # This is a bit tricky with stateless on_bar. A simpler approach is to
            # check the gap based on the previous bar's close that was the overnight_close.
            
            # Let's use the open of the current bar as the "next session open"
            current_open = ctx.bar.open
            
            # Calculate ATR for volatility filter
            atr_val = self._atr(highs, lows, closes, self.volatility_period)
            if atr_val is None or atr_val < ctx.bar.close * self.min_volatility_multiplier:
                return None # Skip if volatility is too low

            gap_pct = (current_open - self.overnight_close) / self.overnight_close * 100

            # Long signal: Significant gap down, but no continuation lower
            if gap_pct < -self.gap_threshold_pct: # Significant gap down
                # Check if price has NOT continued significantly lower from the open
                # We want to buy if the gap down is not being followed by more selling
                # Current price should be above (open - continuation_threshold)
                if ctx.bar.close > current_open * (1 - self.continuation_threshold_pct / 100):
                    self.last_trade_bar_index = ctx.bar_index
                    self.overnight_close = None # Consume the overnight close after using it
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "overnight_gap_down_reversion",
                            "gap_pct": round(gap_pct, 2),
                            "overnight_close": round(self.overnight_close, 2),
                            "current_open": round(current_open, 2),
                            "current_close": round(ctx.bar.close, 2),
                            "atr": round(atr_val, 2),
                        }
                    )

            # Short signal: Significant gap up, but no continuation higher
            elif gap_pct > self.gap_threshold_pct: # Significant gap up
                # Check if price has NOT continued significantly higher from the open
                # We want to sell if the gap up is not being followed by more buying
                # Current price should be below (open + continuation_threshold)
                if ctx.bar.close < current_open * (1 + self.continuation_threshold_pct / 100):
                    self.last_trade_bar_index = ctx.bar_index
                    self.overnight_close = None # Consume the overnight close after using it
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "overnight_gap_up_reversion",
                            "gap_pct": round(gap_pct, 2),
                            "overnight_close": round(self.overnight_close, 2),
                            "current_open": round(current_open, 2),
                            "current_close": round(ctx.bar.close, 2),
                            "atr": round(atr_val, 2),
                        }
                    )

        # Exit logic: if we are in a position and it's approaching the US session start
        if ctx.has_position() and current_hour >= self.us_session_start - 1: # Exit 1 hour before US session
            self.last_trade_bar_index = ctx.bar_index # Cooldown after closing
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": "approaching_us_session_exit",
                    "current_hour": current_hour,
                    "us_session_start": self.us_session_start,
                    "position_direction": ctx.position_direction(),
                }
            )
            
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset overnight close after any position closes to ensure a fresh start for next session
        # This prevents using an old overnight_close if a trade was stopped out early.
        self.overnight_close = None
        # Cooldown is handled in on_bar by self.last_trade_bar_index