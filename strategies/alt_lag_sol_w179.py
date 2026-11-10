from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BetaLagCatchUp(Strategy):
    METADATA = {
        "name": "Beta Lag Catch-Up (SOL)",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2,  # Aim for 2-hour hold
        "warmup_bars": 50,  # Need enough history for EMAs and returns
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_eth_momentum_period = 5  # Lookback for BTC/ETH momentum
        self.sol_lag_period = 3          # Lookback for SOL lag
        self.min_leader_move_pct = 0.5   # Minimum percentage move for BTC/ETH to be considered a "leader"
        self.max_sol_lag_pct = 0.2       # Maximum percentage move for SOL to be considered "lagging"
        self.min_momentum_diff_ratio = 2.0 # BTC/ETH move must be X times greater than SOL's
        self.cooldown_bars = 6           # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _calculate_recent_return(self, values, period):
        if len(values) < period:
            return 0.0
        return ((values[-1] - values[-period]) / values[-period]) * 100.0 if values[-period] != 0 else 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        btc_returns_pct = ctx.features.get("btc_return_pct", 0.0)
        eth_returns_pct = ctx.features.get("eth_return_pct", 0.0)

        # Use recent close prices to calculate SOL's short-term momentum
        sol_recent_return_pct = self._calculate_recent_return(closes, self.sol_lag_period)

        # Check for decisive BTC/ETH movement and SOL lagging
        signal_direction = None
        reason = ""
        metadata = {}

        # Long condition: BTC & ETH up, SOL up but less so
        if btc_returns_pct > self.min_leader_move_pct and eth_returns_pct > self.min_leader_move_pct:
            if sol_recent_return_pct < self.max_sol_lag_pct and sol_recent_return_pct < btc_returns_pct / self.min_momentum_diff_ratio:
                if not ctx.has_position():
                    signal_direction = "long"
                    reason = "beta_lag_long_btc_eth_lead_sol_lag"
                    metadata = {
                        "btc_return_pct": btc_returns_pct,
                        "eth_return_pct": eth_returns_pct,
                        "sol_recent_return_pct": sol_recent_return_pct,
                        "min_leader_move_pct": self.min_leader_move_pct,
                        "max_sol_lag_pct": self.max_sol_lag_pct,
                        "momentum_diff_ratio": self.min_momentum_diff_ratio,
                    }

        # Short condition: BTC & ETH down, SOL down but less so (or even up slightly)
        elif btc_returns_pct < -self.min_leader_move_pct and eth_returns_pct < -self.min_leader_move_pct:
            if sol_recent_return_pct > -self.max_sol_lag_pct and sol_recent_return_pct > btc_returns_pct / self.min_momentum_diff_ratio:
                if not ctx.has_position():
                    signal_direction = "short"
                    reason = "beta_lag_short_btc_eth_lead_sol_lag"
                    metadata = {
                        "btc_return_pct": btc_returns_pct,
                        "eth_return_pct": eth_returns_pct,
                        "sol_recent_return_pct": sol_recent_return_pct,
                        "min_leader_move_pct": self.min_leader_move_pct,
                        "max_sol_lag_pct": self.max_sol_lag_pct,
                        "momentum_diff_ratio": self.min_momentum_diff_ratio,
                    }

        if ctx.has_position():
            # Exit logic: if SOL has caught up or reversed significantly
            if ctx.position_direction() == "long":
                # If SOL has moved significantly since entry, or if leaders reverse
                if sol_recent_return_pct < -self.min_leader_move_pct / 2 or \
                   (btc_returns_pct < 0 and eth_returns_pct < 0):
                    signal_direction = "flat"
                    reason = "long_exit_sol_catch_up_or_leaders_reverse"
                    metadata = {
                        "btc_return_pct": btc_returns_pct,
                        "eth_return_pct": eth_returns_pct,
                        "sol_recent_return_pct": sol_recent_return_pct,
                    }
            elif ctx.position_direction() == "short":
                # If SOL has moved significantly since entry, or if leaders reverse
                if sol_recent_return_pct > self.min_leader_move_pct / 2 or \
                   (btc_returns_pct > 0 and eth_returns_pct > 0):
                    signal_direction = "flat"
                    reason = "short_exit_sol_catch_up_or_leaders_reverse"
                    metadata = {
                        "btc_return_pct": btc_returns_pct,
                        "eth_return_pct": eth_returns_pct,
                        "sol_recent_return_pct": sol_recent_return_pct,
                    }

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=0.7, # Moderate confidence for this pattern
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic implemented for this strategy yet.
        # Can add logging or state resets here if needed.
        pass