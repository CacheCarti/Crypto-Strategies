from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class CrossAssetZScoreReversion(Strategy):
    METADATA = {
        "name": "BTC-SOL Spread Z-Score Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 100,
        "required_features": ["sol_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 60 # For z-score calculation of spread
        self.zscore_entry_threshold = 1.5 # Enter when z-score is extreme
        self.zscore_exit_threshold = 0.5 # Exit when z-score normalizes
        self.cooldown_bars = 5 # Bars to wait after a trade before new entry
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate first trade

        self.btc_returns_history = collections.deque(maxlen=self.lookback_period)
        self.sol_returns_history = collections.deque(maxlen=self.lookback_period)
        self.spread_history = collections.deque(maxlen=self.lookback_period)

    def _stddev(self, values):
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)

    def _zscore(self, values, current_value):
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        std = self._stddev(values)
        if std == 0:
            return 0.0
        return (current_value - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        # Ensure enough data for warmup
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown period after a trade
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Get recent returns for BTC and SOL
        btc_return = ctx.features.get("btc_return_pct", 0.0)
        sol_return = ctx.features.get("sol_return_pct", 0.0)

        # Update historical data
        self.btc_returns_history.append(btc_return)
        self.sol_returns_history.append(sol_return)

        # Only proceed if we have enough history for lookback
        if len(self.btc_returns_history) < self.lookback_period:
            return None

        # Calculate the spread: BTC return - SOL return (SOL is the causal driver)
        # We fade the spread back to zero, assuming SOL leads BTC
        current_spread = btc_return - sol_return
        self.spread_history.append(current_spread)

        # Calculate the z-score of the current spread
        spread_zscore = self._zscore(list(self.spread_history), current_spread)

        # Exit logic
        if ctx.has_position():
            position_direction = ctx.position_direction()
            entry_zscore = ctx.position["metadata"].get("entry_zscore", 0.0)

            # Exit if z-score normalizes (moves towards zero)
            if position_direction == "short" and spread_zscore > -self.zscore_exit_threshold:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "zscore_reversion_exit_short",
                    "current_spread_zscore": spread_zscore,
                    "entry_spread_zscore": entry_zscore
                })
            elif position_direction == "long" and spread_zscore < self.zscore_exit_threshold:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "zscore_reversion_exit_long",
                    "current_spread_zscore": spread_zscore,
                    "entry_spread_zscore": entry_zscore
                })
            return None # Hold position if exit conditions not met

        # Entry logic
        # If BTC has underperformed SOL significantly (negative spread, low z-score), bet on BTC catching up (long BTC)
        if spread_zscore < -self.zscore_entry_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.7, metadata={
                "reason": "zscore_reversion_entry_long",
                "current_spread_zscore": spread_zscore,
                "btc_return_pct": btc_return,
                "sol_return_pct": sol_return,
                "entry_zscore": spread_zscore
            })
        # If BTC has outperformed SOL significantly (positive spread, high z-score), bet on BTC reverting (short BTC)
        elif spread_zscore > self.zscore_entry_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.7, metadata={
                "reason": "zscore_reversion_entry_short",
                "current_spread_zscore": spread_zscore,
                "btc_return_pct": btc_return,
                "sol_return_pct": sol_return,
                "entry_zscore": spread_zscore
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt strategy parameters
        pass