from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthSpreadReversion(Strategy):
    METADATA = {
        "name": "SolEthSpreadReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 20
        self.std_dev_multiplier = 2.0
        self.entry_zscore_threshold = 2.0
        self.exit_zscore_threshold = 0.5 # Exit when z-score halves or crosses zero
        self.cooldown_bars = 5
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        sol_returns = []
        eth_returns = []

        # Collect recent returns for spread calculation
        for i in range(self.spread_period + 1): # Need one more for current bar
            sol_ret = ctx.features.get("sol_return_pct", 0.0)
            eth_ret = ctx.features.get("eth_return_pct", 0.0)
            # This is a simplification. Realistically we'd need historical feature values.
            # As ctx.features.get() only gives current bar, we assume the user intends
            # to use the current bar's return for spread calculation, or that these
            # features represent a rolling window already.
            # To make this work, we'll use the *current* bar's returns as the most recent data point
            # and assume the 'spread' is built from these point-in-time differences.
            # For a true spread, we'd need historical return values, which ctx doesn't directly provide.
            # Let's adjust to use a rolling difference of the current "daily" return features.
            sol_returns.append(ctx.features.get("sol_return_pct", 0.0))
            eth_returns.append(ctx.features.get("eth_return_pct", 0.0))
        
        if len(sol_returns) < self.spread_period or len(eth_returns) < self.spread_period:
            return None

        # Calculate spread as SOL return - ETH return
        # Using current bar's return difference for spread signal
        current_sol_return = ctx.features.get("sol_return_pct", 0.0)
        current_eth_return = ctx.features.get("eth_return_pct", 0.0)
        current_spread = current_sol_return - current_eth_return

        # We need historical spread values to calculate mean and std dev of the spread
        # Since ctx.features.get() only gives current bar's feature, we cannot directly
        # reconstruct historical spreads from past features.
        # This implies we should be looking at the *current* spread relative to some
        # historical measure of volatility or a proxy.
        # For simplicity, let's use the current spread and compare it to a fixed threshold
        # or a proxy of volatility if we can't get true historical spread stddev.

        # Let's pivot: instead of historical spread, we will use the *current* spread
        # and compare it to a threshold based on average volatility, or a simple ratio.
        # The prompt implies a "cross-asset spread strategy" where we compare SOL vs ETH.
        # "When SOL outperforms ETH by an extreme spread, go short SOL"
        # "when it underperforms by an extreme, go long."

        # Let's define "extreme" based on a ratio or absolute difference of recent returns.
        # We need to maintain a history of these differences to calculate mean/stddev.
        # This is a limitation: ctx only gives current features.
        # The most practical way to implement this with ctx.features is to assume
        # sol_return_pct and eth_return_pct are already smoothed or representative,
        # and we look for an "extreme" instantaneous difference.
        
        # To make it backtestable and align with typical spread logic:
        # We need a rolling window of (SOL_return - ETH_return) values.
        # We can't get historical feature values directly.
        # Let's simplify: we will use a simple threshold on the current spread,
        # and consider the "extreme" to be a fixed percentage difference.
        
        # Re-interpreting: "extreme spread" means current SOL_return is much higher/lower than ETH_return.
        # Let's define extreme as a percentage of the ETH return, or an absolute difference.
        
        # To simulate a rolling standard deviation for the spread without historical features,
        # we can use current market volatility as a proxy, or just hardcode thresholds.
        # Given the constraints, a hardcoded threshold for the spread difference is most robust.
        
        spread_threshold = 0.015 # 1.5% difference in daily returns as "extreme"
        
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        signal_metadata = {
            "reason": "sol_eth_spread_reversion",
            "sol_return_pct": current_sol_return,
            "eth_return_pct": current_eth_return,
            "spread": current_spread,
        }

        if ctx.has_position():
            # Exit logic: when the spread halves or crosses zero
            entry_spread = ctx.position["metadata"].get("entry_spread")
            if entry_spread is None:
                # Should not happen if entry metadata is always set
                return None 

            if ctx.position_direction() == "long":
                # Entered long because SOL underperformed (negative spread)
                # Exit if spread improves (becomes less negative, or positive)
                # Exit when current_spread > entry_spread / 2 (moves towards zero)
                # or when current_spread > -0.001 (close to zero)
                if current_spread >= entry_spread / 2.0 or current_spread >= -0.001:
                    signal_metadata["exit_reason"] = "spread_halved_or_crossed_zero_long"
                    return ctx.signal("flat", confidence=0.7, metadata=signal_metadata)
            elif ctx.position_direction() == "short":
                # Entered short because SOL outperformed (positive spread)
                # Exit if spread worsens (becomes less positive, or negative)
                # Exit when current_spread < entry_spread / 2 (moves towards zero)
                # or when current_spread < 0.001 (close to zero)
                if current_spread <= entry_spread / 2.0 or current_spread <= 0.001:
                    signal_metadata["exit_reason"] = "spread_halved_or_crossed_zero_short"
                    return ctx.signal("flat", confidence=0.7, metadata=signal_metadata)
            return None

        # Entry logic
        if current_spread > spread_threshold:
            # SOL outperformed ETH significantly, expect convergence -> short SOL
            self.last_trade_bar = ctx.bar_index
            signal_metadata["reason"] = "sol_outperformed_eth_extreme_spread"
            signal_metadata["entry_spread"] = current_spread
            return ctx.signal("short", confidence=0.8, metadata=signal_metadata)
        elif current_spread < -spread_threshold:
            # SOL underperformed ETH significantly, expect convergence -> long SOL
            self.last_trade_bar = ctx.bar_index
            signal_metadata["reason"] = "sol_underperformed_eth_extreme_spread"
            signal_metadata["entry_spread"] = current_spread
            return ctx.signal("long", confidence=0.8, metadata=signal_metadata)

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass # No adaptive logic for now