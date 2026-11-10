from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadZScore(Strategy):
    METADATA = {
        "name": "EthBtcSpreadZScore",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 48 # 2 days of 1h bars
        self.zscore_threshold = 1.5
        self.cooldown_bars = 12 # 12 hours cooldown
        self.last_trade_bar = -self.cooldown_bars

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
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.spread_period)]
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.spread_period)]

        # We need historical feature data which is not directly available via ctx.features.get() for a range.
        # This is a common limitation. For this strategy, we will approximate by using the current bar's
        # return percentage for the z-score calculation, assuming the engine provides a series if needed.
        # However, the contract only provides current bar features.
        # To get a rolling z-score, we must maintain a history of spreads.

        # Let's assume for simplicity we can get historical returns for calculation.
        # In a real scenario, we'd need to store these in self.
        # Since we cannot access past feature values directly, we'll maintain a list.
        # This is a common pattern for indicator calculation with features.
        if not hasattr(self, 'spread_history'):
            self.spread_history = []

        current_btc_return = ctx.features.get("btc_return_pct", 0.0)
        current_eth_return = ctx.features.get("eth_return_pct", 0.0)
        current_spread = current_eth_return - current_btc_return
        self.spread_history.append(current_spread)

        if len(self.spread_history) > self.spread_period:
            self.spread_history.pop(0) # Keep history to the required period

        if len(self.spread_history) < self.spread_period:
            return None

        spread_zscore = self._zscore(self.spread_history, self.spread_period)

        if ctx.has_position():
            position_direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]
            current_price = ctx.bar.close

            # Exit logic: spread normalizes through zero (or close to it)
            if position_direction == "long" and spread_zscore >= -0.2: # Spread has recovered
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_normalized_long_exit",
                    "spread_zscore": spread_zscore,
                    "current_spread": current_spread,
                    "eth_return": current_eth_return,
                    "btc_return": current_btc_return
                })
            elif position_direction == "short" and spread_zscore <= 0.2: # Spread has recovered
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_normalized_short_exit",
                    "spread_zscore": spread_zscore,
                    "current_spread": current_spread,
                    "eth_return": current_eth_return,
                    "btc_return": current_btc_return
                })
            return None

        # Entry logic
        if spread_zscore < -self.zscore_threshold: # ETH underperforming BTC significantly
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.8, metadata={
                "reason": "eth_btc_spread_oversold_long",
                "spread_zscore": spread_zscore,
                "zscore_threshold": self.zscore_threshold,
                "current_spread": current_spread,
                "eth_return": current_eth_return,
                "btc_return": current_btc_return
            })
        elif spread_zscore > self.zscore_threshold: # ETH outperforming BTC significantly
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.8, metadata={
                "reason": "eth_btc_spread_overbought_short",
                "spread_zscore": spread_zscore,
                "zscore_threshold": self.zscore_threshold,
                "current_spread": current_spread,
                "eth_return": current_eth_return,
                "btc_return": current_btc_return
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust parameters
        pass