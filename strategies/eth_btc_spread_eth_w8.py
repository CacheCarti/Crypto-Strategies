from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadZScore(Strategy):
    METADATA = {
        "name": "ETH-BTC Spread Z-Score",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 60,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 48  # 2 days of 1-hour bars
        self.entry_threshold = 1.5
        self.exit_threshold = 0.0
        self.cooldown_bars = 6
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

        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.zscore_period + 1)]
        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.zscore_period + 1)]

        # We need historical feature data for z-score. Since ctx.features.get only gets current,
        # we need to maintain a history of spreads.
        # For simplicity in this contract, we'll assume a rolling window of the current bar's
        # feature values for z-score calculation. In a real system, features would be historical.
        # Here, we'll use a simplified approach that might not be perfectly accurate for z-score
        # on historical data, but demonstrates the logic.
        # A more robust implementation would require storing historical feature values.
        # Given contract limitations, we'll simulate the z-score over a window of *current* values
        # which is technically incorrect but passes the structure.
        # For actual backtesting, the engine provides historical features.
        # We'll use a placeholder to calculate spread_zscore.
        
        # To properly calculate historical z-score, we'd need a list of historical spreads.
        # Since ctx.features.get() only gives current bar's value, we cannot directly compute
        # a rolling z-score from historical feature data *within this method's scope* without
        # manually storing it. This is a common limitation in simple strategy contracts.
        # For this problem, we will simulate the z-score calculation by creating dummy
        # historical data based on the current bar's features, which is not ideal but
        # adheres to the allowed API.
        
        # A more practical approach given the API is to assume the platform provides a z-score feature.
        # Since it doesn't, we must implement it.
        # We will keep a running list of spreads in self.spread_history.
        if not hasattr(self, 'spread_history'):
            self.spread_history = []

        current_eth_return = ctx.features.get("eth_return_pct", 0.0)
        current_btc_return = ctx.features.get("btc_return_pct", 0.0)
        current_spread = current_eth_return - current_btc_return
        
        self.spread_history.append(current_spread)
        if len(self.spread_history) > self.zscore_period:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.zscore_period:
            return None

        spread_zscore = self._zscore(self.spread_history, self.zscore_period)
        if spread_zscore is None:
            return None

        if ctx.has_position():
            if ctx.position_direction() == "long":
                if spread_zscore >= self.exit_threshold:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "spread_zscore_normalized_long_exit",
                            "spread_zscore": spread_zscore,
                            "eth_return_pct": current_eth_return,
                            "btc_return_pct": current_btc_return,
                        }
                    )
            elif ctx.position_direction() == "short":
                if spread_zscore <= self.exit_threshold:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "spread_zscore_normalized_short_exit",
                            "spread_zscore": spread_zscore,
                            "eth_return_pct": current_eth_return,
                            "btc_return_pct": current_btc_return,
                        }
                    )
            return None # Hold position if not exiting

        # Entry logic
        if spread_zscore > self.entry_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                metadata={
                    "reason": "eth_outperformed_btc_zscore_extreme_short_entry",
                    "spread_zscore": spread_zscore,
                    "eth_return_pct": current_eth_return,
                    "btc_return_pct": current_btc_return,
                    "entry_threshold": self.entry_threshold,
                }
            )
        elif spread_zscore < -self.entry_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                metadata={
                    "reason": "eth_underperformed_btc_zscore_extreme_long_entry",
                    "spread_zscore": spread_zscore,
                    "eth_return_pct": current_eth_return,
                    "btc_return_pct": current_btc_return,
                    "entry_threshold": -self.entry_threshold,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Add adaptive logic or logging here
        pass