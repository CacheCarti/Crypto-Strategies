from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBetaLag(Strategy):
    METADATA = {
        "name": "SOL Beta Lag Strategy",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Aim for a few hours hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 12  # For recent returns (12 hours)
        self.lag_threshold = 0.5  # SOL's move must be less than 50% of leader's average move
        self.min_leader_move_pct = 0.5 # Leaders must move at least 0.5%
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        if ctx.has_position():
            # If position is open, check for time-based exit (short time stop)
            if ctx.bar_index - ctx.position["entry_bar_index"] >= self.declared_hold_seconds / 3600:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "time_stop_exit",
                    "bars_held": ctx.bar_index - ctx.position["entry_bar_index"]
                })
            return None # Hold position

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        btc_returns = ctx.features.get("btc_return_pct", 0.0)
        eth_returns = ctx.features.get("eth_return_pct", 0.0)

        # Get recent SOL returns
        sol_closes = ctx.closes(self.lookback_period)
        if len(sol_closes) < self.lookback_period:
            return None
        sol_recent_return_pct = ((sol_closes[-1] - sol_closes[0]) / sol_closes[0]) * 100.0

        # Check for decisive leader movement in the same direction
        # Both BTC and ETH must move significantly and in the same direction
        long_condition = (btc_returns > self.min_leader_move_pct and eth_returns > self.min_leader_move_pct)
        short_condition = (btc_returns < -self.min_leader_move_pct and eth_returns < -self.min_leader_move_pct)

        signal_direction = None
        reason = ""
        confidence = 0.0

        if long_condition:
            leader_avg_move = (btc_returns + eth_returns) / 2
            # SOL must be lagging behind, but moving in the same direction
            if 0 < sol_recent_return_pct < leader_avg_move * self.lag_threshold:
                signal_direction = "long"
                confidence = 0.7
                reason = "btc_eth_long_sol_lagging_up"
        elif short_condition:
            leader_avg_move = (btc_returns + eth_returns) / 2
            # SOL must be lagging behind, but moving in the same direction (less negative)
            if leader_avg_move * self.lag_threshold < sol_recent_return_pct < 0:
                signal_direction = "short"
                confidence = 0.7
                reason = "btc_eth_short_sol_lagging_down"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "btc_return_pct": btc_returns,
                    "eth_return_pct": eth_returns,
                    "sol_recent_return_pct": sol_recent_return_pct,
                    "leader_avg_move_pct": leader_avg_move,
                    "lag_threshold": self.lag_threshold,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar for cooldown, although it's already set on signal
        # This is more for logging or adaptive sizing if needed
        pass