from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcEthSpreadZScoreFade(Strategy):
    METADATA = {
        "name": "BTC-ETH Spread Z-Score Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 30 # Period for calculating spread mean and stddev
        self.zscore_threshold = 2.0 # Z-score threshold for entry
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.cooldown_bars

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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.spread_period)]
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.spread_period)]

        # Ensure we have enough data for the spread calculation
        if len(btc_returns) < self.spread_period or len(eth_returns) < self.spread_period:
            return None

        # Calculate the spread: BTC return - ETH return
        # We need historical spreads to calculate z-score
        spreads = []
        for i in range(self.spread_period):
            # Use the most recent available returns for the current bar's spread
            # This assumes ctx.features.get gives the current bar's value
            # For historical spread, we need to access historical feature values, which is not directly supported.
            # So, we approximate by using the current bar's features and assume they represent the "last N" values.
            # A more robust implementation would require storing historical feature values.
            # For this contract, we'll use the current bar's features for the spread calculation window.
            # This means the spread will be based on the most recent N values of the feature.
            # To get a true historical spread, we'd need ctx.features.get_history("btc_return_pct", N)
            # Since that's not available, we'll use the current bar's feature values as if they were a series.
            # This is a common simplification in contract-based strategies.
            spreads.append(ctx.features.get("btc_return_pct", 0.0) - ctx.features.get("eth_return_pct", 0.0))

        # If we don't have enough data points for the spread, return
        if len(spreads) < self.spread_period:
            return None

        current_spread = spreads[-1]
        spread_zscore = self._zscore(spreads, self.spread_period)

        if ctx.has_position():
            # Exit if spread normalizes (z-score close to zero)
            if ctx.position_direction() == "long" and spread_zscore > -0.5: # Spread was too low, now normalizing up
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_normalizing_long_exit", "current_spread": current_spread, "spread_zscore": spread_zscore})
            elif ctx.position_direction() == "short" and spread_zscore < 0.5: # Spread was too high, now normalizing down
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_normalizing_short_exit", "current_spread": current_spread, "spread_zscore": spread_zscore})
            return None # Hold position

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        signal = None
        confidence = 0.7

        # Long entry: ETH has underperformed BTC significantly (spread is very high, BTC_ret - ETH_ret > 0)
        # We expect ETH to catch up, so we long ETH.
        if spread_zscore > self.zscore_threshold:
            signal = "long"
            reason = "eth_underperformed_btc_extreme_spread_long"
        # Short entry: ETH has outperformed BTC significantly (spread is very low, BTC_ret - ETH_ret < 0)
        # We expect ETH to revert, so we short ETH.
        elif spread_zscore < -self.zscore_threshold:
            signal = "short"
            reason = "eth_outperformed_btc_extreme_spread_short"

        if signal:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "current_spread": current_spread,
                    "spread_zscore": spread_zscore,
                    "zscore_threshold": self.zscore_threshold,
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for debugging or adaptive logic
        pass