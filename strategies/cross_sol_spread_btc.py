from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetReturnSpreadFadeBTC(Strategy):
    METADATA = {
        "name": "CrossAssetReturnSpreadFadeBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 20 # Period for z-score of spread
        self.zscore_threshold = 1.5 # Z-score threshold for entry
        self.cooldown_bars = 5 # Cooldown after a trade
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
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.spread_period)]
        sol_returns = [ctx.features.get("sol_return_pct", 0.0) for _ in range(self.spread_period)]

        # Ensure we have enough data for the spread calculation
        if any(r is None for r in btc_returns) or any(r is None for r in sol_returns):
            return None

        # Calculate the spread: BTC_return - SOL_return
        # We need historical values to calculate the z-score correctly
        # For simplicity and given the constraints, let's assume btc_return_pct and sol_return_pct
        # are available for the last `spread_period` bars.
        # However, ctx.features.get only gives the current bar's value.
        # To get historical values for z-score, we need to collect them.
        # This requires an internal state or assuming the feature provider gives a list.
        # Given ctx.features.get("key", default) only gives the *current* value,
        # we must adapt. Let's make a simplified assumption:
        # We'll use the current bar's return and compare it to a recent average of spreads.

        # For a proper z-score, we need a history of spreads.
        # We'll maintain a list of past spreads in the strategy's state.
        # This is the most complex part of the strategy given the data access model.

        # Let's collect the current bar's return data first.
        current_btc_return = ctx.features.get("btc_return_pct", 0.0)
        current_sol_return = ctx.features.get("sol_return_pct", 0.0)

        # To calculate a z-score of a spread, we need a history of the spread itself.
        # The contract does not provide historical feature values directly like ctx.closes().
        # We must manually store them.
        # Let's store a rolling window of the spread.
        if not hasattr(self, 'spread_history'):
            self.spread_history = []

        current_spread = current_btc_return - current_sol_return
        self.spread_history.append(current_spread)
        if len(self.spread_history) > self.spread_period:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.spread_period:
            return None # Not enough data to calculate z-score

        spread_zscore = self._zscore(self.spread_history, self.spread_period)

        if ctx.has_position():
            # Exit if spread normalizes (z-score near zero) or if time limit is reached (handled by engine)
            if ctx.position_direction() == "long" and spread_zscore > -0.5: # Spread has widened or normalized
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "spread_normalized_or_reversed_long",
                    "current_spread_zscore": spread_zscore,
                    "btc_return_pct": current_btc_return,
                    "sol_return_pct": current_sol_return
                })
            elif ctx.position_direction() == "short" and spread_zscore < 0.5: # Spread has narrowed or normalized
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "spread_normalized_or_reversed_short",
                    "current_spread_zscore": spread_zscore,
                    "btc_return_pct": current_btc_return,
                    "sol_return_pct": current_sol_return
                })
            return None # Hold existing position

        # Entry logic: fade extreme spread dislocations
        # Long BTC if BTC underperforms SOL significantly (spread is very negative)
        if spread_zscore < -self.zscore_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.7, metadata={
                "reason": "btc_underperformed_sol_extreme_spread_neg",
                "current_spread_zscore": spread_zscore,
                "btc_return_pct": current_btc_return,
                "sol_return_pct": current_sol_return
            })
        # Short BTC if BTC overperforms SOL significantly (spread is very positive)
        elif spread_zscore > self.zscore_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.7, metadata={
                "reason": "btc_overperformed_sol_extreme_spread_pos",
                "current_spread_zscore": spread_zscore,
                "btc_return_pct": current_btc_return,
                "sol_return_pct": current_sol_return
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not strictly needed for this strategy, but good practice for logging or adaptive sizing
        pass