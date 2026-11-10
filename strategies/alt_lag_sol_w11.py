from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BetaLagCatchUp(Strategy):
    METADATA = {
        "name": "Beta Lag Catch-Up (SOL)",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours to catch up
        "warmup_bars": 30,
        "required_features": ["btc_return_pct", "eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 3 # bars for recent returns
        self.min_leader_move_pct = 0.5 # BTC/ETH need to move at least this much
        self.max_lag_ratio = 0.5 # SOL's move relative to leader must be less than this
        self.min_lag_diff_pct = 0.2 # SOL's move must be at least this much less than leader
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        if ctx.has_position():
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Get recent returns for BTC, ETH, and SOL
        btc_returns = ctx.features.get("btc_return_pct", 0.0)
        eth_returns = ctx.features.get("eth_return_pct", 0.0)
        sol_returns = ctx.features.get("sol_return_pct", 0.0)

        # Check for decisive move in BTC and ETH in the same direction
        leader_direction = None
        leader_move_pct = 0.0

        if btc_returns > self.min_leader_move_pct and eth_returns > self.min_leader_move_pct:
            leader_direction = "long"
            leader_move_pct = (btc_returns + eth_returns) / 2
        elif btc_returns < -self.min_leader_move_pct and eth_returns < -self.min_leader_move_pct:
            leader_direction = "short"
            leader_move_pct = (btc_returns + eth_returns) / 2

        if leader_direction is None:
            return None

        # Check if SOL is lagging materially
        if leader_direction == "long":
            # SOL should have moved positively but significantly less than leaders
            if sol_returns > 0 and sol_returns < leader_move_pct * self.max_lag_ratio and (leader_move_pct - sol_returns) > self.min_lag_diff_pct:
                confidence = min(0.8, abs(leader_move_pct - sol_returns) / self.min_leader_move_pct)
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "sol_beta_lag_long_catch_up",
                        "btc_return_pct": btc_returns,
                        "eth_return_pct": eth_returns,
                        "sol_return_pct": sol_returns,
                        "leader_move_pct": leader_move_pct,
                        "lag_diff_pct": leader_move_pct - sol_returns,
                    }
                )
        elif leader_direction == "short":
            # SOL should have moved negatively but significantly less than leaders (less negative)
            if sol_returns < 0 and sol_returns > leader_move_pct * self.max_lag_ratio and (sol_returns - leader_move_pct) > self.min_lag_diff_pct: # leader_move_pct is negative here
                confidence = min(0.8, abs(sol_returns - leader_move_pct) / self.min_leader_move_pct)
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "sol_beta_lag_short_catch_up",
                        "btc_return_pct": btc_returns,
                        "eth_return_pct": eth_returns,
                        "sol_return_pct": sol_returns,
                        "leader_move_pct": leader_move_pct,
                        "lag_diff_pct": sol_returns - leader_move_pct,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally log or adapt logic based on position outcome
        # For this strategy, we just ensure cooldown is handled in on_bar
        pass