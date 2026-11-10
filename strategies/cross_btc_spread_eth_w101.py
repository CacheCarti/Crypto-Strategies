from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetSpreadFade(Strategy):
    METADATA = {
        "name": "Cross Asset Spread Fade (ETH-BTC)",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Target 3-hour hold
        "warmup_bars": 60,  # Need enough bars for 20-period Z-score of 40-period SMA
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 40  # Period for raw spread calculation
        self.zscore_period = 20  # Period for z-score calculation of the spread SMA
        self.entry_zscore_threshold = 1.5  # Z-score magnitude for entry
        self.exit_zscore_threshold = 0.5   # Z-score magnitude for exit (closer to equilibrium)
        self.cooldown_bars = 5             # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # For confidence scaling, we'll use a recent volatility measure
        self.vol_period = 20
        self.min_vol = 0.001 # Minimum volatility to avoid division by zero or extreme confidence

        # Store historical spread values for Z-score
        self.spread_history = []

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
        # Warmup guard
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)

        # Calculate the spread: ETH return minus BTC return
        # A positive spread means ETH outperformed BTC
        # A negative spread means BTC outperformed ETH
        current_spread = eth_ret - btc_ret

        # Update spread history for Z-score calculation
        self.spread_history.append(current_spread)
        if len(self.spread_history) > self.spread_period + self.zscore_period:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.spread_period + self.zscore_period:
            return None

        # Calculate a smoothed spread (SMA of the raw spread)
        smoothed_spread = self._sma(self.spread_history, self.spread_period)
        if smoothed_spread is None:
            return None

        # Calculate the Z-score of the smoothed spread
        z_score = self._zscore(self.spread_history, self.zscore_period)
        if z_score is None:
            return None

        # Calculate recent volatility for confidence scaling
        recent_returns = [ctx.closes(i)[-1] / ctx.closes(i-1)[-1] - 1 for i in range(1, self.vol_period + 1)]
        if len(recent_returns) < self.vol_period:
            return None
        
        # Calculate standard deviation of returns as a proxy for volatility
        volatility = self._stddev(recent_returns, self.vol_period)
        if volatility is None or volatility < self.min_vol:
            volatility = self.min_vol # Cap minimum volatility

        # Confidence is inversely related to Z-score magnitude (closer to 0 is higher confidence for fading)
        # But for entry, we want high confidence when Z-score is extreme.
        # Let's scale confidence based on how far we are from the mean, capped at 1.0.
        # Linear scaling: 0 at entry_zscore_threshold, 1 at 2*entry_zscore_threshold (or similar)
        # Or simpler: confidence increases with absolute Z-score beyond a certain point.
        confidence = min(1.0, (abs(z_score) - self.entry_zscore_threshold) / (self.entry_zscore_threshold * 0.5))
        confidence = max(0.0, confidence) # Ensure confidence is not negative

        # If we have an open position
        if ctx.has_position():
            # Exit logic: if the spread has normalized (Z-score close to zero)
            if ctx.position_direction() == "long": # Fading a negative spread (ETH underperformed BTC)
                if z_score >= -self.exit_zscore_threshold: # Spread has moved back up towards equilibrium
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "spread_normalized_long",
                        "current_zscore": z_score,
                        "entry_zscore": ctx.position["metadata"].get("entry_zscore"),
                        "eth_ret": eth_ret,
                        "btc_ret": btc_ret
                    })
            elif ctx.position_direction() == "short": # Fading a positive spread (ETH outperformed BTC)
                if z_score <= self.exit_zscore_threshold: # Spread has moved back down towards equilibrium
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "spread_normalized_short",
                        "current_zscore": z_score,
                        "entry_zscore": ctx.position["metadata"].get("entry_zscore"),
                        "eth_ret": eth_ret,
                        "btc_ret": btc_ret
                    })
            return None # Hold position

        # Entry logic: fade extreme Z-score dislocations
        if z_score < -self.entry_zscore_threshold: # ETH has significantly underperformed BTC (negative spread)
            # Expect spread to revert upwards -> long ETH
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=confidence, metadata={
                "reason": "extreme_negative_spread_fade",
                "entry_zscore": z_score,
                "eth_ret": eth_ret,
                "btc_ret": btc_ret,
                "volatility": volatility
            })
        elif z_score > self.entry_zscore_threshold: # ETH has significantly outperformed BTC (positive spread)
            # Expect spread to revert downwards -> short ETH
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=confidence, metadata={
                "reason": "extreme_positive_spread_fade",
                "entry_zscore": z_score,
                "eth_ret": eth_ret,
                "btc_ret": btc_ret,
                "volatility": volatility
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details
        pass