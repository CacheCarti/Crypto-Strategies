from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class SpreadZScoreReversion(Strategy):
    METADATA = {
        "name": "Spread Z-Score Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold of 4 hours
        "warmup_bars": 100,  # Need enough bars for Z-score calculation
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 60  # Period for Z-score calculation of the spread
        self.cooldown_bars = 5  # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        if len(slice_v) < 2: # Avoid statistics.stdev on single element list
            return 0.0
        try:
            std = statistics.stdev(slice_v)
        except statistics.StatisticsError: # Handles case where all values are the same
            std = 0.0
        if std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        btc_returns = ctx.features.get("btc_return_pct", 0.0)
        eth_returns = ctx.features.get("eth_return_pct", 0.0)

        # We need historical returns to calculate z-score
        # Get a longer history of BTC and ETH returns for the z-score period
        # Note: ctx.features.get only gives the current bar's value. We need to collect history.
        # This is a common pattern for features that don't have a direct 'history' method.
        # For this strategy, we'll assume the platform can provide historical feature data
        # or that the 'current' feature values are sufficient proxies for the last N bars
        # if the strategy means "z-score of the current spread relative to its recent past".
        # For a truly robust z-score of the spread, we'd need to store past spreads.
        # Let's approximate by using the z-score of the *current* spread against its historical values.

        # To properly calculate Z-score of spread, we need a history of spreads.
        # Since ctx.features.get only gives current bar's value, we must store it.
        # However, the contract only allows storing simple state, and storing long lists
        # of feature values can be memory intensive.
        # For this example, we will calculate the spread and assume _zscore can work on it.
        # In a real scenario, ctx.features.get("feature_name", history_length=N) would be ideal.
        # For now, we will use a simplified approach for demonstration, assuming
        # `_zscore` can operate on a conceptual list of past spreads.

        # Let's simulate a history of spreads. In a live system, this would be `self.spread_history.append(current_spread)`
        # For backtesting, we rely on the helper function which would ideally get a history.
        # Since we don't have historical feature access directly in the contract,
        # we'll adapt the _zscore to work on a sequence derived from price action,
        # or assume the 'return_pct' features are somehow "smoothed" or "representative"
        # of a history if we were to calculate a z-score on them.

        # Given the hard constraint of `ctx.features.get("key", default)` only giving current values,
        # a proper z-score of the *spread* over time is problematic without explicit history storage.
        # Let's adjust the strategy to z-score the *current* relative return,
        # which is more directly calculable with the available API.

        # Define the spread as ETH return - BTC return
        current_spread = eth_returns - btc_returns

        # To calculate the z-score of this spread, we need a history of `current_spread` values.
        # This requires storing state. Let's add `self.spread_history` to `initialize`.
        if not hasattr(self, 'spread_history'):
            self.spread_history = []
        self.spread_history.append(current_spread)
        if len(self.spread_history) > self.zscore_period * 2: # Keep history reasonable
            self.spread_history.pop(0)

        spread_zscore = self._zscore(self.spread_history, self.zscore_period)

        if spread_zscore is None:
            return None

        signal_confidence = 0.0
        signal_direction = "hold"
        reason = ""

        # Long when ETH is underperforming BTC significantly (negative spread z-score)
        # implying ETH is "cheap" relative to BTC, and should revert.
        if spread_zscore < -2.0 and not ctx.has_position():
            signal_direction = "long"
            signal_confidence = min(1.0, abs(spread_zscore) / 3.0) # Scale confidence
            reason = "eth_underperforming_btc_extreme_zscore"
            self.last_trade_bar = ctx.bar_index

        # Short when ETH is outperforming BTC significantly (positive spread z-score)
        # implying ETH is "expensive" relative to BTC, and should revert.
        elif spread_zscore > 2.0 and not ctx.has_position():
            signal_direction = "short"
            signal_confidence = min(1.0, abs(spread_zscore) / 3.0) # Scale confidence
            reason = "eth_overperforming_btc_extreme_zscore"
            self.last_trade_bar = ctx.bar_index

        # Exit conditions:
        # 1. If we have a long position and the spread z-score has normalized (moved above -0.5)
        elif ctx.position_direction() == "long" and spread_zscore > -0.5:
            signal_direction = "flat"
            signal_confidence = 0.6
            reason = "long_spread_zscore_normalized"
            self.last_trade_bar = ctx.bar_index

        # 2. If we have a short position and the spread z-score has normalized (moved below 0.5)
        elif ctx.position_direction() == "short" and spread_zscore < 0.5:
            signal_direction = "flat"
            signal_confidence = 0.6
            reason = "short_spread_zscore_normalized"
            self.last_trade_bar = ctx.bar_index

        if signal_direction != "hold":
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "spread_zscore": spread_zscore,
                    "current_spread": current_spread,
                    "btc_return_pct": btc_returns,
                    "eth_return_pct": eth_returns,
                    "regime": ctx.regime,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for introspection
        pass