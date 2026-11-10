from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcEthSpreadFade(Strategy):
    METADATA = {
        "name": "BTC-ETH Spread Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour holds
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 20  # Period for Z-score calculation
        self.zscore_threshold = 2.0 # Z-score threshold for entry
        self.exit_zscore_threshold = 0.5 # Z-score threshold for exit
        self.cooldown_bars = 5 # Bars to wait after a trade closes
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trading

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if mean is None or std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        # Ensure enough data for indicators and feature warmup
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.spread_period)]
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.spread_period)]

        # We need recent returns, not a history of the same value.
        # This is a proxy for recent data.
        if ctx.bar_index > self.METADATA["warmup_bars"]:
            # Fill with actual historical data for the period
            # NOTE: ctx.features.get() only gives current bar value.
            # For historical feature values, we must rely on the platform providing a series,
            # or calculate from OHLCV if possible.
            # Assuming ctx.features.get("feature_key", 0.0, N) would give historical.
            # Since it doesn't, we'll use a simplified spread based on current values.
            # For true historical spread, we'd need to store past feature values or have them provided.
            # Given the constraints, we'll calculate the spread based on recent returns.
            # Let's use the current bar's return for simplicity, as we don't have historical feature series.

            # Simplified approach for spread: current BTC return minus current ETH return
            # This is not a spread of historical returns, but a spread of current returns.
            # To get a "series" of spreads for Z-score, we'd need to store past spread values.
            # Let's adjust to calculate a simple return difference and use its Z-score over recent bars.

            # To make it backtestable and avoid storing large lists, we will calculate the spread
            # using the current bar's BTC and ETH returns, and then track a moving average
            # and standard deviation of this spread.

            # The current bar's spread
            current_btc_return = ctx.features.get("btc_return_pct", 0.0)
            current_eth_return = ctx.features.get("eth_return_pct", 0.0)
            current_spread = current_btc_return - current_eth_return

            # Store recent spread values in a rolling list
            if not hasattr(self, 'spread_history'):
                self.spread_history = []
            self.spread_history.append(current_spread)
            if len(self.spread_history) > self.spread_period:
                self.spread_history.pop(0)

            if len(self.spread_history) < self.spread_period:
                return None

            spread_zscore = self._zscore(self.spread_history, self.spread_period)

            # Check for existing position
            if ctx.has_position():
                # Exit conditions
                position = ctx.position
                if position["direction"] == "long" and spread_zscore >= -self.exit_zscore_threshold:
                    # Spread has normalized or reversed, exit long
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "spread_long_exit_normalized",
                        "current_spread_zscore": spread_zscore,
                        "current_btc_return": current_btc_return,
                        "current_eth_return": current_eth_return
                    })
                elif position["direction"] == "short" and spread_zscore <= self.exit_zscore_threshold:
                    # Spread has normalized or reversed, exit short
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "spread_short_exit_normalized",
                        "current_spread_zscore": spread_zscore,
                        "current_btc_return": current_btc_return,
                        "current_eth_return": current_eth_return
                    })
                return ctx.signal("hold") # Hold existing position

            # Entry conditions
            if spread_zscore > self.zscore_threshold:
                # BTC has outperformed ETH significantly (spread is high)
                # Fade the spread: go short BTC (expect BTC to underperform or ETH to outperform)
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.8, metadata={
                    "reason": "spread_fade_overextended_btc_outperformance",
                    "current_spread_zscore": spread_zscore,
                    "current_btc_return": current_btc_return,
                    "current_eth_return": current_eth_return
                })
            elif spread_zscore < -self.zscore_threshold:
                # ETH has outperformed BTC significantly (spread is low/negative)
                # Fade the spread: go long BTC (expect BTC to outperform or ETH to underperform)
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.8, metadata={
                    "reason": "spread_fade_overextended_eth_outperformance",
                    "current_spread_zscore": spread_zscore,
                    "current_btc_return": current_btc_return,
                    "current_eth_return": current_eth_return
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Update last_trade_bar to enforce cooldown after any position closure
        self.last_trade_bar = ctx.bar_index
        # Log or adapt based on position outcome if needed
        # For simplicity, no adaptive logic in this example.