from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetBetaConvergence(Strategy):
    METADATA = {
        "name": "Cross-Asset Beta Convergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-hour avg hold
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24 # for returns
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Get recent returns for ETH and BTC
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.lookback_period)]
        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.lookback_period)]

        # We need actual values, not just the default.
        # This is a bit of a hack to ensure we have enough data points.
        # In a real scenario, ctx.features.get should return the actual historical values.
        # For this example, we assume ctx.features.get("eth_return_pct") returns the current bar's value.
        # We'll use the current bar's return for simplicity in this constrained environment.
        current_eth_return = ctx.features.get("eth_return_pct", 0.0)
        current_btc_return = ctx.features.get("btc_return_pct", 0.0)

        # Calculate a "fresh" move for ETH (causal driver)
        # Check if ETH has made a significant move in the current bar,
        # and BTC has not caught up significantly.
        eth_move_threshold = 0.5 # % change
        btc_lag_threshold = 0.2 # % change, BTC's move should be less than this

        signal_direction = None
        reason = ""
        confidence = 0.0

        # Long condition: ETH pumps, BTC lags
        if current_eth_return > eth_move_threshold and current_btc_return < btc_lag_threshold:
            if not ctx.has_position():
                signal_direction = "long"
                reason = "eth_pump_btc_lag_long"
                confidence = min(1.0, current_eth_return / eth_move_threshold * 0.6) # Scale confidence
        # Short condition: ETH dumps, BTC lags
        elif current_eth_return < -eth_move_threshold and current_btc_return > -btc_lag_threshold:
            if not ctx.has_position():
                signal_direction = "short"
                reason = "eth_dump_btc_lag_short"
                confidence = min(1.0, abs(current_eth_return) / eth_move_threshold * 0.6) # Scale confidence

        # Exit condition: If in a position, and BTC has started to catch up, or the original driver reverses
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit if BTC has caught up or ETH reverses
                if current_btc_return > eth_move_threshold * 0.5 or current_eth_return < -0.1:
                    signal_direction = "flat"
                    reason = "beta_convergence_or_eth_reversal_long_exit"
                    confidence = 0.7 # High confidence to exit
            elif ctx.position_direction() == "short":
                # Exit if BTC has caught up or ETH reverses
                if current_btc_return < -eth_move_threshold * 0.5 or current_eth_return > 0.1:
                    signal_direction = "flat"
                    reason = "beta_convergence_or_eth_reversal_short_exit"
                    confidence = 0.7 # High confidence to exit

        if signal_direction:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_eth_return_pct": current_eth_return,
                    "current_btc_return_pct": current_btc_return,
                    "eth_move_threshold": eth_move_threshold,
                    "btc_lag_threshold": btc_lag_threshold,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this simple strategy, just log
        pass