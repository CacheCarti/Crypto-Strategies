from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MonthEndMomentum(Strategy):
    METADATA = {
        "name": "MonthEndMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Intended hold of 2 hours, but can vary
        "warmup_bars": 72, # For 3-day lookback for daily returns
        "required_features": ["eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 48 # Cooldown for 2 days after a month-end trade
        self.last_trade_bar = -self.cooldown_bars
        self.last_month = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        current_month = ctx.bar.timestamp.month

        # Update last_month on the first bar of a new month
        if self.last_month is None:
            self.last_month = current_month
            return None # Skip the first bar to avoid false positives

        # Only consider trading if a new month has started and we're not on cooldown
        if current_month != self.last_month and ctx.bar_index >= self.last_trade_bar + self.cooldown_bars:
            # Check for month-end/month-start transition
            # We need to look at the daily return leading into the new month
            # eth_return_pct is a 24h return, so it captures the previous day's movement.
            # We need enough history for eth_return_pct
            if ctx.bar_index < self.METADATA["warmup_bars"]:
                return None

            # Get 24h return for ETH
            eth_return = ctx.features.get("eth_return_pct", 0.0)

            # Define thresholds for decisive daily movement (e.g., > 1% or < -1%)
            decisive_threshold_pct = 1.0

            # Long condition: Strong positive momentum entering the new month
            if eth_return > decisive_threshold_pct:
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    self.last_month = current_month # Update to avoid re-triggering this month
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "month_start_bullish_momentum",
                            "eth_return_pct": eth_return
                        }
                    )
            # Short condition: Strong negative momentum entering the new month
            elif eth_return < -decisive_threshold_pct:
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    self.last_month = current_month # Update to avoid re-triggering this month
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "month_start_bearish_momentum",
                            "eth_return_pct": eth_return
                        }
                    )
        
        # If we are in a position, just hold.
        if ctx.has_position():
            return ctx.signal("hold")

        # Update last_month for the next iteration if no trade was made this bar
        self.last_month = current_month

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed here for this strategy
        pass