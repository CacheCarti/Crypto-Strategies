from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BetaConvergenceBTC(Strategy):
    METADATA = {
        "name": "Beta Convergence BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60,
        "required_features": ["sol_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24  # Lookback for recent returns
        self.divergence_threshold = 0.005 # 0.5% divergence
        self.min_leader_move = 0.008 # 0.8% move in leader
        self.cooldown_bars = 6
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade if conditions met

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        if len(closes) < self.lookback_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Get recent returns for SOL (leader) and BTC (target)
        sol_returns = [ctx.features.get("sol_return_pct", 0.0) for _ in range(self.lookback_period)]
        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.lookback_period)]

        # We need at least two bars of data for return calculation
        if len(sol_returns) < 2 or len(btc_returns) < 2:
            return None

        # Use the most recent return for decisive move
        current_sol_return = ctx.features.get("sol_return_pct", 0.0)
        current_btc_return = ctx.features.get("btc_return_pct", 0.0)

        # Calculate cumulative returns over a shorter recent window
        recent_window = 4 # e.g., last 4 hours
        cumulative_sol_return = sum(sol_returns[-recent_window:])
        cumulative_btc_return = sum(btc_returns[-recent_window:])

        signal = None
        confidence = 0.6

        # Check for existing position
        if ctx.has_position():
            # Exit if the divergence has largely closed or time is up (handled by horizon_seconds)
            # Or if a strong counter-move happens
            if ctx.position_direction() == "long":
                if current_btc_return < -0.005 or current_sol_return < -0.005: # Strong reversal in either
                    signal = ctx.signal("flat", confidence=0.7, metadata={"reason": "long_exit_strong_reversal", "btc_ret": current_btc_return, "sol_ret": current_sol_return})
            elif ctx.position_direction() == "short":
                if current_btc_return > 0.005 or current_sol_return > 0.005: # Strong reversal in either
                    signal = ctx.signal("flat", confidence=0.7, metadata={"reason": "short_exit_strong_reversal", "btc_ret": current_btc_return, "sol_ret": current_sol_return})
            return signal # If we decided to exit, return the signal, otherwise hold

        # Entry logic
        # Long condition: SOL made a significant positive move, but BTC has not caught up
        if cumulative_sol_return > self.min_leader_move and \
           (cumulative_sol_return - cumulative_btc_return) > self.divergence_threshold:
            signal = ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "beta_convergence_long",
                    "leader_move": cumulative_sol_return,
                    "target_lag": cumulative_btc_return,
                    "divergence": cumulative_sol_return - cumulative_btc_return,
                }
            )
            self.last_trade_bar = ctx.bar_index

        # Short condition: SOL made a significant negative move, but BTC has not caught up
        elif cumulative_sol_return < -self.min_leader_move and \
             (cumulative_btc_return - cumulative_sol_return) > self.divergence_threshold: # BTC is less negative than SOL
            signal = ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "beta_convergence_short",
                    "leader_move": cumulative_sol_return,
                    "target_lag": cumulative_btc_return,
                    "divergence": cumulative_btc_return - cumulative_sol_return,
                }
            )
            self.last_trade_bar = ctx.bar_index

        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for potential adaptive logic in the future
        # For now, just a placeholder. Cooldown is handled in on_bar.
        pass