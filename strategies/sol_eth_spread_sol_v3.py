from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthSpreadReversion(Strategy):
    METADATA = {
        "name": "SOL/ETH Spread Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold_long = -0.025  # SOL underperforms ETH by 2.5%
        self.spread_threshold_short = 0.025  # SOL outperforms ETH by 2.5%
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

        # For tracking spread transitions
        self.previous_spread = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown period after a signal or close
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        sol_returns = ctx.features.get("sol_return_pct", 0.0)
        eth_returns = ctx.features.get("eth_return_pct", 0.0)

        # Calculate the spread: SOL return relative to ETH return
        # A positive spread means SOL outperformed ETH
        # A negative spread means SOL underperformed ETH
        current_spread = sol_returns - eth_returns

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Check for existing position to manage exits
        if ctx.has_position():
            position = ctx.position
            entry_spread = position["metadata"].get("entry_spread", 0.0)

            # Exit condition: spread halves from entry or crosses zero
            if position["direction"] == "long":
                # Entry was on extreme underperformance (negative spread)
                # Exit if spread improves significantly (halves the negative distance to zero) or turns positive
                if current_spread >= entry_spread / 2 or current_spread >= 0:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_position_exit_spread_converged",
                        "current_spread": current_spread,
                        "entry_spread": entry_spread,
                        "sol_return_pct": sol_returns,
                        "eth_return_pct": eth_returns
                    })
            elif position["direction"] == "short":
                # Entry was on extreme outperformance (positive spread)
                # Exit if spread declines significantly (halves the positive distance to zero) or turns negative
                if current_spread <= entry_spread / 2 or current_spread <= 0:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_position_exit_spread_converged",
                        "current_spread": current_spread,
                        "entry_spread": entry_spread,
                        "sol_return_pct": sol_returns,
                        "eth_return_pct": eth_returns
                    })
            # If no exit condition met, hold
            return None

        # Entry logic
        # Long when SOL significantly underperforms ETH, expecting reversion
        if current_spread < self.spread_threshold_long and self.previous_spread >= self.spread_threshold_long:
            signal_direction = "long"
            confidence = 0.8
            reason = "sol_eth_spread_extreme_underperformance_reversion"
        # Short when SOL significantly outperforms ETH, expecting reversion
        elif current_spread > self.spread_threshold_short and self.previous_spread <= self.spread_threshold_short:
            signal_direction = "short"
            confidence = 0.8
            reason = "sol_eth_spread_extreme_outperformance_reversion"

        self.previous_spread = current_spread

        if signal_direction:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_spread": current_spread,
                    "sol_return_pct": sol_returns,
                    "eth_return_pct": eth_returns,
                    "entry_spread": current_spread # Store spread at entry for exit logic
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt parameters
        pass