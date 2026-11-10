from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetReturnSpread(Strategy):
    METADATA = {
        "name": "Cross Asset Return Spread (ETH-SOL)",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, targeting 1-2 bars hold time
        "warmup_bars": 60, # Need enough bars for 20-period z-score and 4-bar cooldown
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 20
        self.cooldown_bars = 4
        self.last_signal_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trading
        self.last_signal_direction = None
        self.last_zscore_cross_above_zero = False
        self.last_zscore_cross_below_zero = False

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
        closes = ctx.closes(self.zscore_period)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_signal_bar_index + self.cooldown_bars:
            return None

        sol_returns = []
        eth_returns = []
        # Gather recent eth_return_pct and sol_return_pct for z-score calculation
        for i in range(self.zscore_period):
            sol_returns.append(ctx.features.get("sol_return_pct", 0.0, offset=i))
            eth_returns.append(ctx.features.get("eth_return_pct", 0.0, offset=i))

        if len(sol_returns) < self.zscore_period or len(eth_returns) < self.zscore_period:
            return None

        # Calculate spread: ETH return minus SOL return
        spread_values = [e - s for e, s in zip(eth_returns, sol_returns)]

        # Calculate z-score of the spread
        spread_zscore = self._zscore(spread_values, self.zscore_period)

        if spread_zscore is None:
            return None

        # Define thresholds for extreme dislocations
        long_threshold = -1.5 # ETH underperforming SOL significantly
        short_threshold = 1.5  # ETH outperforming SOL significantly

        signal_direction = None
        confidence = 0.0
        reason = ""
        metadata = {
            "spread_zscore": spread_zscore,
            "current_eth_return_pct": eth_returns[-1],
            "current_sol_return_pct": sol_returns[-1],
            "regime": ctx.regime
        }

        # Entry logic: Fade extreme z-score dislocations
        if not ctx.has_position():
            if spread_zscore < long_threshold:
                # ETH is significantly underperforming SOL, expect mean reversion
                signal_direction = "long"
                confidence = min(1.0, abs(spread_zscore) / 2.0) # Scale confidence
                reason = "eth_sol_spread_zscore_oversold"
            elif spread_zscore > short_threshold:
                # ETH is significantly outperforming SOL, expect mean reversion
                signal_direction = "short"
                confidence = min(1.0, abs(spread_zscore) / 2.0) # Scale confidence
                reason = "eth_sol_spread_zscore_overbought"

            if signal_direction:
                self.last_signal_bar_index = ctx.bar_index
                self.last_signal_direction = signal_direction
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={**metadata, "reason": reason}
                )

        # Exit logic: When spread normalizes (z-score crosses zero)
        else:
            current_zscore_cross_above_zero = spread_zscore > 0 and self.last_zscore_cross_below_zero
            current_zscore_cross_below_zero = spread_zscore < 0 and self.last_zscore_cross_above_zero

            if ctx.position_direction() == "long" and current_zscore_cross_above_zero:
                signal_direction = "flat"
                reason = "spread_zscore_normalized_long_exit"
            elif ctx.position_direction() == "short" and current_zscore_cross_below_zero:
                signal_direction = "flat"
                reason = "spread_zscore_normalized_short_exit"

            # Update cross flags for next bar
            self.last_zscore_cross_above_zero = spread_zscore > 0
            self.last_zscore_cross_below_zero = spread_zscore < 0

            if signal_direction:
                self.last_signal_bar_index = ctx.bar_index
                self.last_signal_direction = None # Reset direction after exit
                return ctx.signal(
                    signal_direction,
                    confidence=0.7, # Moderate confidence for exits
                    metadata={**metadata, "reason": reason}
                )
        
        # Update cross flags if no signal was generated
        self.last_zscore_cross_above_zero = spread_zscore > 0
        self.last_zscore_cross_below_zero = spread_zscore < 0

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust future behavior
        pass