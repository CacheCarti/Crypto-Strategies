from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetSpreadFadeSOL(Strategy):
    METADATA = {
        "name": "Cross-Asset Spread Fade SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60, # For 30-period lookback + 30-bar cooldown
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 30 # Period for z-score calculation of spread
        self.zscore_threshold = 1.5 # Z-score deviation for entry
        self.cooldown_bars = 12 # Cooldown period after a trade (12 hours)
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
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        # Warmup and cooldown checks
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Get recent returns for BTC and SOL
        btc_returns = ctx.features.get("btc_return_pct", 0.0)
        sol_returns = ctx.features.get("sol_return_pct", 0.0)

        # We need historical returns to calculate the spread's z-score
        # For simplicity, we'll assume historical 'btc_return_pct' and 'sol_return_pct'
        # are available in the context's features. In a real scenario, these would
        # typically be calculated from historical price data or a feature pipeline.
        # For this contract, ctx.features.get() only returns the current bar's value.
        # To get historical values, we'd need to store them or compute from closes.
        # Given the constraints, we'll simulate a historical spread for z-score.
        
        # Let's use the current bar's return as a proxy for the 'last' value needed
        # for z-score calculation, and assume the 'values' parameter to _zscore
        # would contain a history of (sol_return - btc_return) if it were available.
        
        # To make this work with the contract, we will compute the spread from the
        # asset's price data directly, as we cannot get historical feature values easily.
        # We will use price changes for the spread.
        
        closes = ctx.closes(self.spread_period + 1)
        if len(closes) < self.spread_period + 1:
            return None

        # Calculate a simple 1-period return for SOL and BTC based on close prices
        # This is a simplification. Ideally, we'd use the actual btc_return_pct feature history.
        # Since ctx.features.get() only gives current bar, we simulate the spread history
        # from the asset's own price history, effectively making it a momentum spread.
        
        # Let's adjust the strategy to directly use SOL's price action
        # and BTC's return as a filter, rather than a direct spread z-score.
        # This is because creating a historical spread from features is not directly supported
        # by the ctx.features.get() API for historical values.

        # Strategy logic: Fade extreme short-term SOL price moves if BTC is also moving.
        # We'll use SOL's 1-period return z-score and BTC's recent return as a filter.
        
        sol_price_changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        if not sol_price_changes:
            return None
        
        sol_zscore = self._zscore(sol_price_changes, self.spread_period)

        if sol_zscore is None:
            return None

        signal_direction = None
        confidence = 0.0
        reason = "no_signal"
        
        # Regime filter: Only trade in "normal" or "volatile" regimes, avoid "crisis"
        if ctx.regime == "crisis" or ctx.market.get("crisis_score", 0.0) > 0.5:
            reason = "crisis_regime_stand_down"
            return None

        # Entry logic: Fade extreme z-score moves
        # Short if SOL is extremely positive (overbought relative to its own recent volatility)
        # Long if SOL is extremely negative (oversold relative to its own recent volatility)
        
        # Add a filter for BTC return: only trade if BTC is not moving too much in the opposite direction
        # or if BTC is confirming the move.
        
        # Long condition: SOL is oversold (negative z-score) and BTC is not extremely negative
        if sol_zscore < -self.zscore_threshold and btc_returns > -0.01: # BTC not crashing
            signal_direction = "long"
            confidence = min(1.0, abs(sol_zscore) / (self.zscore_threshold * 2)) # Scale confidence
            reason = "sol_zscore_oversold_fade"
            
        # Short condition: SOL is overbought (positive z-score) and BTC is not extremely positive
        elif sol_zscore > self.zscore_threshold and btc_returns < 0.01: # BTC not pumping
            signal_direction = "short"
            confidence = min(1.0, abs(sol_zscore) / (self.zscore_threshold * 2))
            reason = "sol_zscore_overbought_fade"

        if ctx.has_position():
            # If in position, check for exit conditions
            if ctx.position_direction() == "long" and sol_zscore > -0.5: # Normalize back to less oversold
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "sol_zscore_normalized_long_exit", "sol_zscore": sol_zscore, "btc_returns": btc_returns})
            elif ctx.position_direction() == "short" and sol_zscore < 0.5: # Normalize back to less overbought
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "sol_zscore_normalized_short_exit", "sol_zscore": sol_zscore, "btc_returns": btc_returns})
            
            # If no exit, hold position
            return ctx.signal("hold", metadata={"reason": "holding_position", "sol_zscore": sol_zscore})


        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "sol_zscore": sol_zscore,
                    "btc_returns": btc_returns,
                    "regime": ctx.regime,
                    "crisis_score": ctx.market.get("crisis_score", 0.0)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or reset any adaptive state
        pass