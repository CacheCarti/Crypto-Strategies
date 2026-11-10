from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcSolSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "BTC-SOL Spread Mean Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60,
        "required_features": ["sol_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 20 # Period for spread calculation
        self.zscore_period = 40 # Period for z-score calculation
        self.zscore_threshold = 1.5 # Z-score threshold for entry
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.recent_spreads = []

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
            return 0.0 # Default to 0.0 before enough data
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if mean is None or std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        btc_returns = ctx.features.get("btc_return_pct", 0.0)
        sol_returns = ctx.features.get("sol_return_pct", 0.0)

        # Calculate the spread (SOL % return - BTC % return)
        # We need to ensure both are fresh and not stale zero values
        if btc_returns == 0.0 and sol_returns == 0.0:
            return None # Skip if both returns are default/stale

        current_spread = sol_returns - btc_returns
        self.recent_spreads.append(current_spread)
        if len(self.recent_spreads) > self.zscore_period:
            self.recent_spreads.pop(0)

        if len(self.recent_spreads) < self.zscore_period:
            return None

        current_zscore = self._zscore(self.recent_spreads, self.zscore_period)

        # Regime gating: only trade in 'normal' or 'volatile' regimes and not during 'crisis'
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "CRISIS" or market_regime == "MELTDOWN":
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit logic
        if ctx.has_position():
            pos_direction = ctx.position_direction()
            entry_zscore = ctx.position.get("metadata", {}).get("entry_zscore")

            # Exit if spread normalizes (z-score moves back towards zero)
            if pos_direction == "long" and current_zscore > -0.5: # Spread was too negative, now less negative
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_normalizing_after_long",
                    "current_zscore": current_zscore,
                    "entry_zscore": entry_zscore,
                    "current_spread": current_spread
                })
            elif pos_direction == "short" and current_zscore < 0.5: # Spread was too positive, now less positive
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_normalizing_after_short",
                    "current_zscore": current_zscore,
                    "entry_zscore": entry_zscore,
                    "current_spread": current_spread
                })
            return None # Hold position if not exiting

        # Entry logic
        # Long BTC when SOL underperforms BTC significantly (negative spread, low z-score)
        if current_zscore < -self.zscore_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.8, metadata={
                "reason": "sol_underperformed_btc_extreme_zscore",
                "current_zscore": current_zscore,
                "zscore_threshold": self.zscore_threshold,
                "current_spread": current_spread,
                "btc_return_pct": btc_returns,
                "sol_return_pct": sol_returns,
                "entry_zscore": current_zscore
            })

        # Short BTC when SOL outperforms BTC significantly (positive spread, high z-score)
        elif current_zscore > self.zscore_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.8, metadata={
                "reason": "sol_outperformed_btc_extreme_zscore",
                "current_zscore": current_zscore,
                "zscore_threshold": self.zscore_threshold,
                "current_spread": current_spread,
                "btc_return_pct": btc_returns,
                "sol_return_pct": sol_returns,
                "entry_zscore": current_zscore
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic needed for this strategy's current version
        pass