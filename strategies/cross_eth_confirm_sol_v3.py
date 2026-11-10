from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetETHConfirmSOL(Strategy):
    METADATA = {
        "name": "Cross Asset ETH Confirm SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 30,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars
        self.last_eth_return_sign = 0 # 0 for neutral, 1 for positive, -1 for negative

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown period check
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
        sol_return_pct = ctx.features.get("sol_return_pct", 0.0)

        # We need recent returns to make a decision
        if abs(eth_return_pct) < 0.1 and abs(sol_return_pct) < 0.1: # Threshold for "freshness"
            return None

        current_eth_sign = 0
        if eth_return_pct > 0.1: # Positive threshold
            current_eth_sign = 1
        elif eth_return_pct < -0.1: # Negative threshold
            current_eth_sign = -1

        # Check for transition in ETH return sign
        if current_eth_sign == self.last_eth_return_sign:
            return None # Only trigger on transition

        self.last_eth_return_sign = current_eth_sign

        if not ctx.has_position():
            # Entry logic
            if eth_return_pct > 0.2 and sol_return_pct > 0.2: # Both show strong positive returns
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "eth_confirm_sol_positive_return",
                        "eth_return_pct": eth_return_pct,
                        "sol_return_pct": sol_return_pct,
                        "price": ctx.bar.close
                    }
                )
            elif eth_return_pct < -0.2 and sol_return_pct < -0.2: # Both show strong negative returns
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "eth_confirm_sol_negative_return",
                        "eth_return_pct": eth_return_pct,
                        "sol_return_pct": sol_return_pct,
                        "price": ctx.bar.close
                    }
                )
        else:
            # Exit logic: if leader (ETH) disagrees or relationship normalizes
            if ctx.position_direction() == "long":
                if eth_return_pct < -0.1 or sol_return_pct < -0.1: # ETH turns negative or SOL turns negative
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "eth_sol_reversion_exit_long",
                            "eth_return_pct": eth_return_pct,
                            "sol_return_pct": sol_return_pct,
                            "price": ctx.bar.close
                        }
                    )
            elif ctx.position_direction() == "short":
                if eth_return_pct > 0.1 or sol_return_pct > 0.1: # ETH turns positive or SOL turns positive
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "eth_sol_reversion_exit_short",
                            "eth_return_pct": eth_return_pct,
                            "sol_return_pct": sol_return_pct,
                            "price": ctx.bar.close
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or reset any state if needed
        pass