from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapReversion(Strategy):
    METADATA = {
        "name": "Session Gap Reversion BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours, targeting session close
        "warmup_bars": 50, # Need enough bars for 2 session closes
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_us_session_close = None
        self.traded_today = False
        self.cooldown_bars = 0
        self.COOLDOWN_PERIOD = 3 # Bars to wait after a trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset traded_today flag at the start of a new UTC day
        if current_hour == 0 and self.traded_today:
            self.traded_today = False
            self.last_us_session_close = None # Reset for fresh session detection

        # Cooldown logic
        if self.cooldown_bars > 0:
            self.cooldown_bars -= 1
            return None

        # Logic for exiting before UTC 0
        if ctx.has_position() and current_hour == 23: # Exit before the day ends
            if ctx.position["direction"] == "long":
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_price": ctx.bar.close})
            elif ctx.position["direction"] == "short":
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_price": ctx.bar.close})

        # Identify late US session (UTC 18-24)
        if current_hour >= 18 and current_hour < 24:
            # Store the close of the last bar of the late US session
            self.last_us_session_close = ctx.bar.close
            return None # No entry logic during this session, only store close

        # Entry logic: Early UTC session (0-6)
        if current_hour >= 0 and current_hour <= 6 and not ctx.has_position() and not self.traded_today:
            if self.last_us_session_close is None:
                return None # Need a prior session close to compare

            # Calculate the gap as a percentage
            gap_pct = ((ctx.bar.open - self.last_us_session_close) / self.last_us_session_close) * 100

            # Gap threshold (e.g., 0.1% gap)
            GAP_THRESHOLD_PCT = 0.15 # Adjust based on domain/volatility

            # Continuation check: has the price moved further in the gap direction?
            # We check if the current close has moved past the open in the gap direction
            # If gap was positive, and current price is higher than open, it's continuation
            # If gap was negative, and current price is lower than open, it's continuation

            # Simplified continuation check: if the current bar's close is moving away from the prior session close
            # i.e., if gap up, and price is still above open, or if gap down, and price is still below open
            
            # For reversion, we want to see the gap and then a lack of continuation, or even a reversal attempt
            # A simple way to check "lack of continuation" for a gap up: price has moved lower than its open
            # A simple way to check "lack of continuation" for a gap down: price has moved higher than its open

            # Reversion opportunity if:
            # 1. Significant gap up AND current price is below the current bar's open (attempting to close gap)
            # 2. Significant gap down AND current price is above the current bar's open (attempting to close gap)

            if gap_pct > GAP_THRESHOLD_PCT: # Gap up
                if ctx.bar.close < ctx.bar.open: # Price moving down, suggesting reversion
                    self.traded_today = True
                    self.cooldown_bars = self.COOLDOWN_PERIOD
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "gap_up_reversion",
                            "gap_pct": round(gap_pct, 2),
                            "prior_session_close": round(self.last_us_session_close, 2),
                            "current_open": round(ctx.bar.open, 2),
                            "current_close": round(ctx.bar.close, 2),
                            "session_hour": current_hour
                        }
                    )
            elif gap_pct < -GAP_THRESHOLD_PCT: # Gap down
                if ctx.bar.close > ctx.bar.open: # Price moving up, suggesting reversion
                    self.traded_today = True
                    self.cooldown_bars = self.COOLDOWN_PERIOD
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "gap_down_reversion",
                            "gap_pct": round(gap_pct, 2),
                            "prior_session_close": round(self.last_us_session_close, 2),
                            "current_open": round(ctx.bar.open, 2),
                            "current_close": round(ctx.bar.close, 2),
                            "session_hour": current_hour
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after any position closes
        self.cooldown_bars = self.COOLDOWN_PERIOD
        # Log position details for analysis if needed
        # print(f"Position closed: {position['direction']} {position['return_bps']} bps in {position['bars_held']} bars.")
        pass