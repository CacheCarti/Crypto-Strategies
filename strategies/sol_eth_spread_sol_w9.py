from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthSpreadReversion(Strategy):
    METADATA = {
        "name": "SOL-ETH Spread Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 20
        self.zscore_threshold = 1.5
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        sol_returns = []
        eth_returns = []
        
        # Collect historical returns for Z-score calculation
        for i in range(self.spread_period + 1):
            sol_ret = ctx.features.get("sol_return_pct", 0.0, offset=i)
            eth_ret = ctx.features.get("eth_return_pct", 0.0, offset=i)
            if sol_ret is not None and eth_ret is not None:
                sol_returns.append(sol_ret)
                eth_returns.append(eth_ret)
            else:
                # Not enough data for historical returns
                return None

        if len(sol_returns) < self.spread_period + 1:
            return None

        # Calculate current spread and historical spreads
        current_sol_return = sol_returns[-1]
        current_eth_return = eth_returns[-1]
        current_spread = current_sol_return - current_eth_return
        
        historical_spreads = []
        for i in range(len(sol_returns)):
            historical_spreads.append(sol_returns[i] - eth_returns[i])
        
        if len(historical_spreads) < self.spread_period:
            return None

        spread_zscore = self._zscore(historical_spreads, self.spread_period)

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        if ctx.has_position():
            position = ctx.position
            entry_spread = position["metadata"].get("entry_spread")
            
            if entry_spread is None:
                # Should not happen, but a safeguard
                return None

            # Exit condition: spread reverts halfway or crosses zero
            if position["direction"] == "long":
                # Entered long when SOL underperformed (spread was very negative)
                # Exit when spread is less negative (closer to zero or positive)
                # Target: spread > entry_spread / 2 or spread > 0
                if current_spread > entry_spread / 2.0 or current_spread >= 0.0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "long_exit_spread_reverted",
                        "current_spread": current_spread,
                        "entry_spread": entry_spread,
                        "spread_zscore": spread_zscore
                    })
            elif position["direction"] == "short":
                # Entered short when SOL outperformed (spread was very positive)
                # Exit when spread is less positive (closer to zero or negative)
                # Target: spread < entry_spread / 2 or spread < 0
                if current_spread < entry_spread / 2.0 or current_spread <= 0.0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "short_exit_spread_reverted",
                        "current_spread": current_spread,
                        "entry_spread": entry_spread,
                        "spread_zscore": spread_zscore
                    })
            return None # Hold position if exit conditions not met

        # Entry logic
        if spread_zscore is not None:
            if spread_zscore < -self.zscore_threshold:
                # SOL has significantly underperformed ETH, expect convergence (SOL to rise)
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "sol_underperformed_eth_extreme",
                        "current_spread": current_spread,
                        "spread_zscore": spread_zscore,
                        "sol_return_pct": current_sol_return,
                        "eth_return_pct": current_eth_return,
                        "entry_spread": current_spread
                    }
                )
            elif spread_zscore > self.zscore_threshold:
                # SOL has significantly outperformed ETH, expect convergence (SOL to fall)
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "sol_outperformed_eth_extreme",
                        "current_spread": current_spread,
                        "spread_zscore": spread_zscore,
                        "sol_return_pct": current_sol_return,
                        "eth_return_pct": current_eth_return,
                        "entry_spread": current_spread
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: log or adapt based on position outcome
        pass