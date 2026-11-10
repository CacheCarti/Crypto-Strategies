from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "SOL/ETH Return Spread Mean Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.spread_threshold = 0.03 # 3% extreme spread
        self.exit_spread_ratio = 0.5 # Exit when spread halves
        self.spread_history = []
        self.history_length = 48 # Keep 2 days of spread history for context

    def _calculate_spread(self, ctx: BarContext) -> Optional[float]:
        sol_return = ctx.features.get("sol_return_pct", 0.0)
        eth_return = ctx.features.get("eth_return_pct", 0.0)
        if sol_return is not None and eth_return is not None:
            return sol_return - eth_return
        return None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        current_spread = self._calculate_spread(ctx)
        if current_spread is None:
            return None

        # Maintain spread history
        self.spread_history.append(current_spread)
        if len(self.spread_history) > self.history_length:
            self.spread_history.pop(0)

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit logic
        if ctx.has_position():
            position_spread = ctx.position["metadata"].get("entry_spread")
            if position_spread is not None:
                if ctx.position_direction() == "long":
                    # Long: entered because SOL underperformed (negative spread)
                    # Exit if spread has moved towards zero significantly or crossed zero
                    if current_spread >= 0 or abs(current_spread) <= abs(position_spread) * self.exit_spread_ratio:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("flat", confidence=0.8, metadata={
                            "reason": "long_spread_reverted_or_halved",
                            "entry_spread": position_spread,
                            "current_spread": current_spread
                        })
                elif ctx.position_direction() == "short":
                    # Short: entered because SOL outperformed (positive spread)
                    # Exit if spread has moved towards zero significantly or crossed zero
                    if current_spread <= 0 or abs(current_spread) <= abs(position_spread) * self.exit_spread_ratio:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("flat", confidence=0.8, metadata={
                            "reason": "short_spread_reverted_or_halved",
                            "entry_spread": position_spread,
                            "current_spread": current_spread
                        })
            return ctx.signal("hold")

        # Entry logic
        # Long condition: SOL underperforms ETH significantly
        if current_spread < -self.spread_threshold:
            # Check for a recent upward turn in the spread to confirm mean reversion
            if len(self.spread_history) > 2 and self.spread_history[-1] > self.spread_history[-2]:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.7, metadata={
                    "reason": "sol_underperformed_eth_mean_reversion_long",
                    "sol_return_pct": ctx.features.get("sol_return_pct", 0.0),
                    "eth_return_pct": ctx.features.get("eth_return_pct", 0.0),
                    "entry_spread": current_spread
                })

        # Short condition: SOL outperforms ETH significantly
        elif current_spread > self.spread_threshold:
            # Check for a recent downward turn in the spread to confirm mean reversion
            if len(self.spread_history) > 2 and self.spread_history[-1] < self.spread_history[-2]:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.7, metadata={
                    "reason": "sol_outperformed_eth_mean_reversion_short",
                    "sol_return_pct": ctx.features.get("sol_return_pct", 0.0),
                    "eth_return_pct": ctx.features.get("eth_return_pct", 0.0),
                    "entry_spread": current_spread
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust parameters
        pass