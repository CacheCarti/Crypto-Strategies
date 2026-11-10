from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetReturnSpread(Strategy):
    METADATA = {
        "name": "CrossAssetReturnSpread",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 30 # Period for z-score of the spread
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

        if ctx.has_position():
            return None # Hold position until exit conditions are met or time/SL/TP

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None # Cooldown period

        # Get recent returns for BTC and ETH
        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.spread_period)]
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.spread_period)]

        # Ensure we have enough data points for z-score calculation
        if len(btc_returns) < self.spread_period or len(eth_returns) < self.spread_period:
            return None

        # Calculate the spread: BTC return - ETH return
        spread_values = []
        for i in range(self.spread_period):
            # We need to get historical values for the z-score.
            # For simplicity and to fit within line limits, we'll use the current bar's
            # feature value repeated for the period, assuming features are relatively stable
            # or the z-score is calculated on the single latest spread value against its history.
            # A more robust approach would require storing past feature values.
            # Given the constraint, we will compute z-score on the *current* spread relative to recent historical spreads.
            # To simulate historical spread, we'll use the latest available feature values.
            # This is a simplification due to constraints.
            # A proper implementation would require storing historical btc_return_pct and eth_return_pct.
            # For this context, we will compute the z-score of the current spread against a history of spreads
            # derived from the current bar's features, meaning we need to fetch historical feature data.
            # Since ctx.features.get only gets the current bar's value, we must approximate.
            # Let's assume the required_features array has the historical values for the period.
            # This is NOT how features work. Features are for the CURRENT BAR.
            # To get historical features, we'd need a helper that stores them.

            # Re-evaluating: ctx.features.get("key", default) only gives the current bar's feature.
            # We need a history of spreads to calculate a z-score.
            # The only way to get a history is to store it ourselves or use OHLCV data.
            # Since the prompt specifies btc_return_pct and eth_return_pct, we must use them.
            # To compute a z-score, we need a series of `spread_t = btc_return_pct_t - eth_return_pct_t`.
            # Let's use the closes() function to approximate historical feature values, which is NOT ideal but necessary for the contract.
            # This is a critical point: if features are only available for the current bar, z-scoring them requires
            # manually collecting a history of them in the strategy class.

            # Let's implement a simple way to store historical spreads.
            # This requires an array on `self`.
            # We'll use a circular buffer or similar.
            pass # The actual implementation will be below this re-evaluation

        # Initialize or update spread history
        if not hasattr(self, '_spread_history'):
            self._spread_history = []

        current_btc_return = ctx.features.get("btc_return_pct", 0.0)
        current_eth_return = ctx.features.get("eth_return_pct", 0.0)
        current_spread = current_btc_return - current_eth_return

        self._spread_history.append(current_spread)
        if len(self._spread_history) > self.spread_period:
            self._spread_history.pop(0) # Keep history limited

        if len(self._spread_history) < self.spread_period:
            return None # Not enough history for z-score

        spread_zscore = self._zscore(self._spread_history, self.spread_period)

        # Market regime filters
        current_regime = ctx.market.get("regime", "NORMAL")
        current_trend_regime = ctx.market.get("trend_regime", "neutral")

        # Stand down in crisis or strong bear/bull trends (we want mean reversion, not trend following)
        if current_regime in ["CRISIS", "MELTDOWN"]:
            return None
        if current_trend_regime in ["bull", "bear"] and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
            return None

        signal_confidence = 0.0
        reason = ""
        direction = "hold"

        # Long when ETH is underperforming BTC significantly (spread is very positive, meaning BTC >> ETH)
        if spread_zscore > self.zscore_threshold:
            direction = "long"
            signal_confidence = min(0.9, (spread_zscore - self.zscore_threshold) * 0.3 + 0.3) # Scale confidence
            reason = "cross_asset_spread_eth_underperforming"
        # Short when ETH is outperforming BTC significantly (spread is very negative, meaning ETH >> BTC)
        elif spread_zscore < -self.zscore_threshold:
            direction = "short"
            signal_confidence = min(0.9, (-spread_zscore - self.zscore_threshold) * 0.3 + 0.3) # Scale confidence
            reason = "cross_asset_spread_eth_overperforming"

        if direction != "hold":
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                direction,
                confidence=signal_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "spread_zscore": round(spread_zscore, 2),
                    "current_btc_return_pct": round(current_btc_return, 2),
                    "current_eth_return_pct": round(current_eth_return, 2),
                    "market_regime": current_regime,
                    "trend_regime": current_trend_regime,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to enforce cooldown after any position closure
        self.last_trade_bar = ctx.bar_index
        # No adaptive sizing or complex state resets for this strategy,
        # but this method is here for completeness.
        pass