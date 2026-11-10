from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthSolSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthSol Spread Mean Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold
        "warmup_bars": 100,
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 60 # Period for z-scoring the spread
        self.zscore_threshold = 2.0 # Z-score deviation for entry
        self.cooldown_bars = 12 # Cooldown after a trade
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

        eth_returns = ctx.features.get("eth_return_pct", 0.0)
        sol_returns = ctx.features.get("sol_return_pct", 0.0)

        # Ensure we have fresh return data for the current bar
        if eth_returns == 0.0 and sol_returns == 0.0:
            return None

        # Calculate the spread: ETH return minus SOL return
        # A positive spread means ETH outperformed SOL
        # A negative spread means SOL outperformed ETH
        current_spread = eth_returns - sol_returns

        # Store recent spreads to calculate z-score
        if not hasattr(self, 'spread_history'):
            self.spread_history = []
        self.spread_history.append(current_spread)
        if len(self.spread_history) > self.spread_period * 2: # Keep more for buffer
            self.spread_history = self.spread_history[-(self.spread_period * 2):]

        if len(self.spread_history) < self.spread_period:
            return None

        spread_zscore = self._zscore(self.spread_history, self.spread_period)

        if ctx.has_position():
            # Exit if the spread has mean-reverted (z-score close to zero)
            if ctx.position_direction() == "long" and spread_zscore >= -0.5: # Spread normalized or became positive
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_normalized_long_exit",
                    "current_spread": current_spread,
                    "spread_zscore": spread_zscore,
                    "eth_return_pct": eth_returns,
                    "sol_return_pct": sol_returns
                })
            elif ctx.position_direction() == "short" and spread_zscore <= 0.5: # Spread normalized or became negative
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_normalized_short_exit",
                    "current_spread": current_spread,
                    "spread_zscore": spread_zscore,
                    "eth_return_pct": eth_returns,
                    "sol_return_pct": sol_returns
                })
            return None # Hold position if not exiting

        # Entry logic: fade extreme z-scores
        # If ETH significantly underperformed SOL (negative z-score), go long ETH (expect ETH to catch up)
        if spread_zscore < -self.zscore_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.75, metadata={
                "reason": "eth_sol_spread_oversold_long",
                "current_spread": current_spread,
                "spread_zscore": spread_zscore,
                "zscore_threshold": self.zscore_threshold,
                "eth_return_pct": eth_returns,
                "sol_return_pct": sol_returns
            })
        # If ETH significantly outperformed SOL (positive z-score), go short ETH (expect ETH to fall back)
        elif spread_zscore > self.zscore_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.75, metadata={
                "reason": "eth_sol_spread_overbought_short",
                "current_spread": current_spread,
                "spread_zscore": spread_zscore,
                "zscore_threshold": self.zscore_threshold,
                "eth_return_pct": eth_returns,
                "sol_return_pct": sol_returns
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust parameters
        # For simplicity, we just log here.
        pass