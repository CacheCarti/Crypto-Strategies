from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcSpreadFade(Strategy):
    METADATA = {
        "name": "SOL BTC Spread Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 20  # Period for Z-score calculation
        self.zscore_threshold = 2.0  # Z-score deviation for entry
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store recent spread values for Z-score calculation
        self.spread_history = []
        self.last_zscore = 0.0

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
            return 0.0 # Avoid division by zero, return neutral
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        btc_return = ctx.features.get("btc_return_pct", 0.0)
        sol_return = ctx.features.get("sol_return_pct", 0.0)

        # Calculate the spread: BTC return minus SOL return
        # We expect SOL to revert to BTC's movement
        current_spread = btc_return - sol_return
        self.spread_history.append(current_spread)
        if len(self.spread_history) > self.spread_period * 2: # Keep history a bit longer than needed
            self.spread_history.pop(0)

        if len(self.spread_history) < self.spread_period:
            return None

        current_zscore = self._zscore(self.spread_history, self.spread_period)

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # If we have an open position, check for exit conditions
        if ctx.has_position():
            # Exit if Z-score reverts towards zero
            if ctx.position_direction() == "long" and current_zscore > -0.5: # Spread was too negative, now less negative
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "spread_reverted_long", "current_zscore": current_zscore, "entry_zscore": ctx.position["metadata"].get("entry_zscore")})
            elif ctx.position_direction() == "short" and current_zscore < 0.5: # Spread was too positive, now less positive
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "spread_reverted_short", "current_zscore": current_zscore, "entry_zscore": ctx.position["metadata"].get("entry_zscore")})
            # Also exit if the relationship goes against us further (stop-loss will handle extremes, this is for faster exits)
            elif ctx.position_direction() == "long" and current_zscore > self.zscore_threshold / 2: # Spread gets even more negative
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "spread_worsened_long_exit", "current_zscore": current_zscore, "entry_zscore": ctx.position["metadata"].get("entry_zscore")})
            elif ctx.position_direction() == "short" and current_zscore < -self.zscore_threshold / 2: # Spread gets even more positive
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "spread_worsened_short_exit", "current_zscore": current_zscore, "entry_zscore": ctx.position["metadata"].get("entry_zscore")})
            return None # Hold position if no exit condition met

        # Entry logic: Fade extreme Z-score deviations
        signal = None
        confidence = 0.0

        # Long entry: SOL underperformed BTC significantly (spread is very positive, z-score high)
        # We expect SOL to catch up, so buy SOL.
        if current_zscore > self.zscore_threshold and self.last_zscore <= self.zscore_threshold: # Transition detection
            signal = "long"
            confidence = min(0.9, (current_zscore - self.zscore_threshold) / (self.zscore_threshold * 0.5) + 0.5) # Scale confidence
            self.last_trade_bar = ctx.bar_index

        # Short entry: SOL outperformed BTC significantly (spread is very negative, z-score low)
        # We expect SOL to revert, so sell SOL.
        elif current_zscore < -self.zscore_threshold and self.last_zscore >= -self.zscore_threshold: # Transition detection
            signal = "short"
            confidence = min(0.9, (abs(current_zscore) - self.zscore_threshold) / (self.zscore_threshold * 0.5) + 0.5) # Scale confidence
            self.last_trade_bar = ctx.bar_index
        
        self.last_zscore = current_zscore

        if signal:
            return ctx.signal(
                signal,
                confidence=confidence,
                metadata={
                    "reason": f"spread_zscore_fade_{signal}",
                    "current_zscore": current_zscore,
                    "btc_return_pct": btc_return,
                    "sol_return_pct": sol_return,
                    "entry_zscore": current_zscore
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally log or adjust parameters based on position outcome
        pass