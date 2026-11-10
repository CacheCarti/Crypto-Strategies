from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeOpenReversion(Strategy):
    METADATA = {
        "name": "Europe Open Reversion ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Max hold until 12 UTC, roughly 2-5 hours
        "warmup_bars": 24, # Need previous day's close
    }

    def initialize(self, ctx: BarContext) -> None:
        self.traded_today = False
        self.last_trade_bar = -1
        self.cooldown_bars = 5 # Cooldown period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(24) # Need previous day's close
        opens = ctx.opens(1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset traded_today flag at the start of a new day (UTC 00:00)
        if current_hour == 0 and not self.traded_today:
            self.traded_today = False
        # If it's a new day, allow trading
        if ctx.bar_index > self.last_trade_bar + self.cooldown_bars:
            self.traded_today = False

        # Trading window: European morning (UTC 7-12)
        if 7 <= current_hour < 12 and not self.traded_today:
            # We need the close of the previous UTC day (end of 23:00 bar)
            # This is roughly 8 bars ago if we are at 7 UTC (previous day's 23:00 bar)
            # Or we can look for the close of the bar at 23:00 UTC of the previous day
            # For simplicity, let's assume the "prior session close" refers to the close of the bar at 23:00 UTC yesterday.
            # This means we need at least 8 bars of history for the 7 UTC bar to get yesterday's 23:00 close (index 0 is 8 bars ago)
            # Find the close of yesterday's 23:00 UTC bar
            prior_day_close = None
            for i in range(len(ctx.closes(24)) - 1, -1, -1): # Iterate backwards from recent to old
                bar_timestamp = ctx.bar.timestamp - (len(ctx.closes(24)) - 1 - i) * 3600 # Approx timestamp for that bar
                if bar_timestamp.day != current_day and bar_timestamp.hour == 23:
                    prior_day_close = ctx.closes(24)[i]
                    break

            if prior_day_close is None:
                return None # Not enough history to find yesterday's 23:00 close

            current_open = ctx.bar.open
            gap_pct = ((current_open - prior_day_close) / prior_day_close) * 100

            # Gap threshold (e.g., 0.5% gap)
            gap_threshold_pct = 0.3

            # Check for gap without continuation
            # Long: gap down, price tries to revert
            if gap_pct < -gap_threshold_pct:
                # If current price is still below prior_day_close but not moving lower
                # Or if current price is trying to move back up
                if ctx.bar.close > current_open: # Some upward movement from open
                    self.traded_today = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        horizon_seconds=(12 - current_hour) * 3600, # Exit by 12 UTC
                        metadata={
                            "reason": "europe_open_reversion_long",
                            "gap_pct": round(gap_pct, 2),
                            "prior_day_close": round(prior_day_close, 2),
                            "current_open": round(current_open, 2),
                            "close_vs_open": round(ctx.bar.close - current_open, 2),
                            "entry_hour": current_hour
                        }
                    )
            # Short: gap up, price tries to revert
            elif gap_pct > gap_threshold_pct:
                # If current price is still above prior_day_close but not moving higher
                # Or if current price is trying to move back down
                if ctx.bar.close < current_open: # Some downward movement from open
                    self.traded_today = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        horizon_seconds=(12 - current_hour) * 3600, # Exit by 12 UTC
                        metadata={
                            "reason": "europe_open_reversion_short",
                            "gap_pct": round(gap_pct, 2),
                            "prior_day_close": round(prior_day_close, 2),
                            "current_open": round(current_open, 2),
                            "close_vs_open": round(ctx.bar.close - current_open, 2),
                            "entry_hour": current_hour
                        }
                    )

        # Exit any open position at 12:00 UTC if still held
        if ctx.has_position() and current_hour == 12:
            return ctx.signal(
                "flat",
                confidence=0.9,
                metadata={
                    "reason": "session_end_exit",
                    "exit_hour": current_hour,
                    "position_direction": ctx.position_direction()
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt based on position close
        pass