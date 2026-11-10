from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadReversion(Strategy):
    METADATA = {
        "name": "ETH-BTC Spread Z-Score Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 100,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_window = 60
        self.zscore_threshold = 1.5
        self.cooldown_bars = 5
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
            return 0.0 # Return 0 if std dev is zero or not enough data
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.spread_window)]
        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.spread_window)]

        # To get historical feature values, we need to fetch them for the window
        # This is a simplification; in a real scenario, features would need to be stored or retrieved historically.
        # For this contract, ctx.features.get() only gives the current bar's value.
        # We will assume for this implementation that eth_return_pct and btc_return_pct are the current bar's 24h return,
        # and we need to reconstruct the historical series for the z-score.
        # Since we cannot access historical features directly in this contract,
        # we will use the most recent N values of the current bar's return, which is a common proxy.
        # This means we need to store a history of returns.

        if not hasattr(self, 'eth_return_history'):
            self.eth_return_history = []
            self.btc_return_history = []

        self.eth_return_history.append(ctx.features.get("eth_return_pct", 0.0))
        self.btc_return_history.append(ctx.features.get("btc_return_pct", 0.0))

        if len(self.eth_return_history) > self.spread_window:
            self.eth_return_history.pop(0)
            self.btc_return_history.pop(0)

        if len(self.eth_return_history) < self.spread_window:
            return None

        # Calculate the spread history
        spread_history = [e - b for e, b in zip(self.eth_return_history, self.btc_return_history)]

        if len(spread_history) < self.spread_window:
            return None

        current_spread_zscore = self._zscore(spread_history, self.spread_window)

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit logic: if we have a position and spread z-score crosses zero (normalizes)
        if ctx.has_position():
            if ctx.position_direction() == "long" and current_spread_zscore >= 0.0:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_zscore_normalized_long_exit",
                    "current_zscore": current_spread_zscore
                })
            elif ctx.position_direction() == "short" and current_spread_zscore <= 0.0:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_zscore_normalized_short_exit",
                    "current_zscore": current_spread_zscore
                })
            return None

        # Entry logic
        signal = None
        confidence = 0.8 # Base confidence

        if current_spread_zscore < -self.zscore_threshold:
            # ETH is underperforming BTC significantly, go long ETH
            signal = ctx.signal("long", confidence=confidence, metadata={
                "reason": "eth_btc_spread_oversold",
                "spread_zscore": current_spread_zscore,
                "threshold": -self.zscore_threshold
            })
        elif current_spread_zscore > self.zscore_threshold:
            # ETH is outperforming BTC significantly, go short ETH
            signal = ctx.signal("short", confidence=confidence, metadata={
                "reason": "eth_btc_spread_overbought",
                "spread_zscore": current_spread_zscore,
                "threshold": self.zscore_threshold
            })

        if signal:
            self.last_trade_bar = ctx.bar_index
        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: could adjust zscore_threshold or cooldown based on performance
        pass