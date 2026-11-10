from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class BtcSolSpreadFade(Strategy):
    METADATA = {
        "name": "BTC-SOL Spread Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 100,
        "required_features": ["sol_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 60 # Bars for Z-score calculation (~60 hours)
        self.zscore_threshold = 1.5 # Entry threshold for Z-score
        self.exit_zscore_threshold = 0.5 # Exit threshold for Z-score normalization
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store recent spread values for Z-score calculation
        self.spread_history = collections.deque(maxlen=self.lookback_period)

    def _stddev(self, values):
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)

    def _zscore(self, values, current_value):
        if len(values) < self.lookback_period:
            return 0.0 # Not enough data for meaningful Z-score
        mean = sum(values) / len(values)
        std = self._stddev(values)
        if std == 0:
            return 0.0
        return (current_value - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        if len(closes) < self.lookback_period + 1:
            return None

        # Ensure enough bars for feature data
        if ctx.bar_index < self.warmup_bars:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        sol_ret = ctx.features.get("sol_return_pct", 0.0)

        # Calculate the spread: BTC return - SOL return
        # A positive spread means BTC outperformed SOL
        # A negative spread means SOL outperformed BTC
        current_spread = btc_ret - sol_ret
        self.spread_history.append(current_spread)

        if len(self.spread_history) < self.lookback_period:
            return None # Not enough history for Z-score calculation

        current_zscore = self._zscore(list(self.spread_history), current_spread)

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit logic
        if ctx.has_position():
            position_direction = ctx.position_direction()
            entry_zscore = ctx.position.get("metadata", {}).get("entry_zscore")

            # Exit if Z-score normalizes (moves back towards zero)
            if position_direction == "long" and current_zscore < self.exit_zscore_threshold:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_normalized_long_exit",
                    "current_zscore": current_zscore,
                    "entry_zscore": entry_zscore,
                    "btc_return_pct": btc_ret,
                    "sol_return_pct": sol_ret
                })
            elif position_direction == "short" and current_zscore > -self.exit_zscore_threshold:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_normalized_short_exit",
                    "current_zscore": current_zscore,
                    "entry_zscore": entry_zscore,
                    "btc_return_pct": btc_ret,
                    "sol_return_pct": sol_ret
                })
            return ctx.signal("hold")

        # Entry logic
        signal_confidence = 0.8 # Base confidence

        # Long BTC: BTC underperformed SOL significantly (negative spread, low Z-score)
        # We expect the spread to revert, meaning BTC will catch up or SOL will fall
        if current_zscore < -self.zscore_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=signal_confidence, metadata={
                "reason": "spread_reversion_long_entry",
                "current_zscore": current_zscore,
                "zscore_threshold": self.zscore_threshold,
                "btc_return_pct": btc_ret,
                "sol_return_pct": sol_ret,
                "entry_zscore": current_zscore
            })

        # Short BTC: BTC outperformed SOL significantly (positive spread, high Z-score)
        # We expect the spread to revert, meaning BTC will fall or SOL will catch up
        elif current_zscore > self.zscore_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=signal_confidence, metadata={
                "reason": "spread_reversion_short_entry",
                "current_zscore": current_zscore,
                "zscore_threshold": self.zscore_threshold,
                "btc_return_pct": btc_ret,
                "sol_return_pct": sol_ret,
                "entry_zscore": current_zscore
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis or adaptive logic (not implemented here)
        pass