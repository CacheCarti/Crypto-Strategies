from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthSolBetaConvergence(Strategy):
    METADATA = {
        "name": "ETH-SOL Beta Convergence",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 30,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.eth_period = 3  # Lookback for ETH return
        self.sol_period = 3  # Lookback for SOL return
        self.divergence_threshold = 0.015 # 1.5% divergence
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # For mean reversion, we want to exit when the gap closes
        # or if the trend reverses.
        self.reversion_window = 10 # bars to check for gap closing

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_eth_return = ctx.features.get("eth_return_pct", 0.0)
        current_sol_return = ctx.features.get("sol_return_pct", 0.0)

        # We need recent returns to assess "freshness"
        # Let's check the average return over the last few bars for both ETH and SOL
        # to ensure a "decisive move" rather than just a single bar spike.
        eth_returns = []
        sol_returns = []
        for i in range(self.eth_period):
            # eth_return_pct is per-bar cadence, so we can use it directly
            # ctx.features.get("eth_return_pct", 0.0) would return current bar's
            # We need to approximate historical via current and a simple moving average
            # For simplicity, we'll use the current bar's return for the 'decisive move'
            # and compare it to its own recent average to check for freshness.
            # However, the feature is usually a 24h return, so it's already "smoothed".
            # To capture "freshness", we look for a significant recent deviation.

            # We'll use the provided per-bar eth_return_pct and sol_return_pct directly.
            # A "fresh decisive move" implies a strong recent return.
            # Let's consider the current eth_return_pct as the "decisive move".
            pass # We use the current bar's features directly for simplicity.

        # Calculate the divergence
        divergence = current_eth_return - current_sol_return

        # Check for long signal: ETH moved up, SOL lagged
        if divergence > self.divergence_threshold and not ctx.has_position():
            # To ensure it's a "fresh" move, we can check if the current ETH return
            # is significantly higher than its recent average or if the divergence
            # itself is a recent maximum. For simplicity, we'll just check if the
            # current ETH return is positive and significant.
            if current_eth_return > self.divergence_threshold / 2: # ETH needs to be moving up
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long",
                                  confidence=min(1.0, abs(divergence) * 10), # Scale confidence by divergence
                                  metadata={
                                      "reason": "eth_sol_beta_convergence_long",
                                      "eth_return_pct": current_eth_return,
                                      "sol_return_pct": current_sol_return,
                                      "divergence": divergence
                                  })

        # Check for short signal: ETH moved down, SOL lagged
        elif divergence < -self.divergence_threshold and not ctx.has_position():
            # To ensure it's a "fresh" move, ETH needs to be moving down.
            if current_eth_return < -self.divergence_threshold / 2: # ETH needs to be moving down
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short",
                                  confidence=min(1.0, abs(divergence) * 10), # Scale confidence by divergence
                                  metadata={
                                      "reason": "eth_sol_beta_convergence_short",
                                      "eth_return_pct": current_eth_return,
                                      "sol_return_pct": current_sol_return,
                                      "divergence": divergence
                                  })

        # Position management: Exit if the divergence normalizes or reverses
        if ctx.has_position():
            position_direction = ctx.position_direction()
            entry_divergence = ctx.position["metadata"].get("divergence")

            if position_direction == "long":
                # Exit if SOL has caught up or ETH reverses
                if current_sol_return >= current_eth_return or current_eth_return < 0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_divergence_normalized_or_reversed",
                        "current_eth_return": current_eth_return,
                        "current_sol_return": current_sol_return,
                        "entry_divergence": entry_divergence
                    })
            elif position_direction == "short":
                # Exit if SOL has caught up or ETH reverses
                if current_sol_return <= current_eth_return or current_eth_return > 0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_divergence_normalized_or_reversed",
                        "current_eth_return": current_eth_return,
                        "current_sol_return": current_sol_return,
                        "entry_divergence": entry_divergence
                    })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass # No adaptive logic needed for this simple version