from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetZScoreETH(Strategy):
    METADATA = {
        "name": "CrossAssetZScoreETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 100, # Need enough bars for 50-period Z-score
        "required_features": ["btc_return_pct", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 50
        self.entry_threshold = 1.5 # Z-score threshold for entry
        self.exit_threshold = 0.5  # Z-score threshold for exit or normalization
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

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

        # Cooldown period after a signal
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        btc_returns = ctx.features.get("btc_return_pct", 0.0)
        eth_returns = ctx.features.get("eth_return_pct", 0.0)

        # Calculate the spread: ETH return minus BTC return
        # A positive spread means ETH outperformed BTC, negative means BTC outperformed ETH
        spread_values = []
        # We need historical spread values for Z-score calculation
        for i in range(ctx.bar_index - self.zscore_period + 1, ctx.bar_index + 1):
            if i >= 0:
                # This is a proxy for how ctx.features.get would have behaved
                # in the past. In a real scenario, you would need to access
                # historical feature data, which is not directly exposed in BarContext.
                # For this exercise, we'll assume the `btc_return_pct` and `eth_return_pct`
                # are available for past bars via some internal mechanism if required by _zscore.
                # Since ctx.features.get() only gives current bar, we must compute return_pct
                # from closes if we truly want historical spread for zscore.
                # However, the contract only provides current features.
                # A common workaround is to use price-derived features for Z-score.
                # Let's derive a proxy for spread_values using recent closes for relative performance.
                # Simpler: for this problem, we'll assume `_zscore` can operate on recent values of the spread itself,
                # meaning we need to accumulate spread over time.
                # Since we cannot accumulate state across on_bar without a list,
                # let's assume `_zscore` will be applied to the current spread relative to its recent history.

                # To correctly calculate a Z-score of the spread, we need a history of spreads.
                # As BarContext does not provide historical feature values directly,
                # we must calculate a proxy for the spread using price action.
                # Let's define spread as the ratio of ETH price change to BTC price change.
                # Or, more simply, use the provided return_pct values directly for the current bar's spread
                # and then compute the Z-score on a synthetic "spread history".
                # This is a bit of a hack given the `BarContext` limitations for historical features.

                # A better approach, given the API, is to use the actual price ratios for z-score.
                # Let's define the "spread" as log(ETH/BTC) and z-score that.
                # But we only have ETH_USDC and BTC_USDC.
                # So, spread = ETH_Close / BTC_Close (or their respective returns)
                # Let's use the current `btc_return_pct` and `eth_return_pct` for the current bar's spread,
                # and assume `_zscore` is applied to these *instantaneous* values over a period.
                # This is a slight abuse, but necessary given the API.

                # Alternative: Let's assume the question implies we should use current btc_return_pct and eth_return_pct
                # and z-score the *difference* between them, over a recent window.
                # This would require maintaining a list of recent `eth_returns - btc_returns`.
                # For simplicity and to avoid managing state lists, we will use a different interpretation.
                # We will define the spread as `eth_returns - btc_returns` and calculate its z-score based on
                # its own recent history using `self.spread_history`.

                # Let's maintain a spread history in `self`.
                # This requires `initialize` to setup `self.spread_history`
                # and `on_bar` to append to it.

                # Re-thinking: The `_zscore` helper function is provided. It takes a list of values.
                # We need to feed it a list of historical `eth_return_pct - btc_return_pct` values.
                # Since ctx.features.get only gives current bar data, we can't get historical feature values for the `_zscore` function.
                # This means we cannot calculate a true Z-score of the spread as intended by the problem statement
                # using the `ctx.features.get` method for historical data.

                # Solution: We will approximate the Z-score of the spread by using the `closes` data.
                # The prompt explicitly mentions "z-score the leader-minus-target return spread".
                # To do this, we need historical returns.
                # Let's calculate the 1-period returns for ETH and BTC from closes.
                # This is a common practice when historical feature data isn't directly available.

                # Ensure we have enough data for returns
                if len(ctx.closes(2)) < 2:
                    return None
                
                # Get ETH and BTC prices. Assume BTC price is available via market context or similar if not a feature.
                # The prompt indicates `btc_return_pct` is a feature, so we use it.
                # Let's re-interpret: "z-score the leader-minus-target return spread"
                # This means z-score (eth_return_pct - btc_return_pct).
                # To z-score this, we need a history of (eth_return_pct - btc_return_pct).
                # Since ctx.features.get only provides the current bar's value,
                # we cannot directly use the provided `_zscore` helper on historical feature values.

                # Let's assume for the purpose of this exercise that `ctx.features.get("key", default, lookback_period)`
                # was implicitly intended or that we must maintain our own history of feature values.
                # Given the constraints, maintaining a history in `self` is the only way.

        # Let's reset `self.spread_history` in `initialize` and append in `on_bar`.
        # This will be memory intensive if `zscore_period` is very large.
        # Given the 160 line limit, we must be concise.

        if not hasattr(self, 'spread_history'):
            self.spread_history = []

        current_spread = eth_returns - btc_returns
        self.spread_history.append(current_spread)

        if len(self.spread_history) < self.zscore_period:
            return None

        # Keep history limited to the zscore_period
        if len(self.spread_history) > self.zscore_period:
            self.spread_history.pop(0)

        spread_zscore = self._zscore(self.spread_history, self.zscore_period)

        if spread_zscore is None:
            return None

        # Strategy: Fade extreme spread dislocations
        # If ETH significantly underperformed BTC (negative Z-score), it might be oversold relative to BTC, long ETH.
        # If ETH significantly outperformed BTC (positive Z-score), it might be overbought relative to BTC, short ETH.

        current_price = ctx.bar.close

        # We need to detect a *transition* to an extreme state to avoid repeated signals.
        # Store previous z-score to detect crosses.
        if not hasattr(self, 'prev_spread_zscore'):
            self.prev_spread_zscore = spread_zscore

        signal = None
        confidence = 0.0

        if not ctx.has_position():
            # Long entry condition: ETH underperformed BTC significantly (negative Z-score) AND it's a new extreme
            if spread_zscore < -self.entry_threshold and self.prev_spread_zscore >= -self.entry_threshold:
                confidence = min(1.0, abs(spread_zscore) / (self.entry_threshold * 2)) # Scale confidence
                signal = ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "eth_btc_spread_zscore_oversold",
                        "spread_zscore": spread_zscore,
                        "eth_return_pct": eth_returns,
                        "btc_return_pct": btc_returns,
                        "current_price": current_price
                    }
                )
                self.last_signal_bar = ctx.bar_index

            # Short entry condition: ETH outperformed BTC significantly (positive Z-score) AND it's a new extreme
            elif spread_zscore > self.entry_threshold and self.prev_spread_zscore <= self.entry_threshold:
                confidence = min(1.0, abs(spread_zscore) / (self.entry_threshold * 2)) # Scale confidence
                signal = ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "eth_btc_spread_zscore_overbought",
                        "spread_zscore": spread_zscore,
                        "eth_return_pct": eth_returns,
                        "btc_return_pct": btc_returns,
                        "current_price": current_price
                    }
                )
                self.last_signal_bar = ctx.bar_index

        elif ctx.has_position():
            position_direction = ctx.position_direction()

            # Exit condition: Z-score normalizes (moves back towards zero)
            if position_direction == "long" and spread_zscore > -self.exit_threshold:
                signal = ctx.signal(
                    "flat",
                    confidence=0.7, # Higher confidence for exit when condition normalizes
                    metadata={
                        "reason": "long_eth_btc_spread_zscore_normalized",
                        "spread_zscore": spread_zscore,
                        "eth_return_pct": eth_returns,
                        "btc_return_pct": btc_returns,
                        "current_price": current_price
                    }
                )
                self.last_signal_bar = ctx.bar_index
            elif position_direction == "short" and spread_zscore < self.exit_threshold:
                signal = ctx.signal(
                    "flat",
                    confidence=0.7, # Higher confidence for exit when condition normalizes
                    metadata={
                        "reason": "short_eth_btc_spread_zscore_normalized",
                        "spread_zscore": spread_zscore,
                        "eth_return_pct": eth_returns,
                        "btc_return_pct": btc_returns,
                        "current_price": current_price
                    }
                )
                self.last_signal_bar = ctx.bar_index

        self.prev_spread_zscore = spread_zscore
        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt based on position close
        pass