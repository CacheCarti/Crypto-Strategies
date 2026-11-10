from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthSolBetaConvergence(Strategy):
    METADATA = {
        "name": "EthSolBetaConvergence",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60,
        "required_features": ["eth_return_pct", "sol_return_pct"],
        "version": "1.0",
        "description": "Enters SOL long/short when ETH makes a decisive move and SOL has not yet converged, expecting beta convergence. Uses a transition-based trigger to avoid overtrading.",
    }

    def initialize(self, ctx: BarContext) -> None:
        self.eth_return_period = 3 # bars for ETH decisive move
        self.sol_return_period = 3 # bars for SOL recent lag
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # Thresholds for decisive moves and convergence
        self.eth_decisive_threshold = 0.005 # 0.5% move in eth_return_pct
        self.sol_lag_threshold = 0.002 # SOL's move must be less than this in the same direction

        # EMA for trend filtering
        self.ema_period = 20

        # Store previous ETH return states to detect transitions
        self.prev_eth_return_direction = 0 # 0: neutral, 1: positive, -1: negative
        self.prev_sol_return_direction = 0

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Check cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        closes = ctx.closes(self.ema_period)
        if len(closes) < self.ema_period:
            return None

        current_price = ctx.bar.close
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # Get recent ETH and SOL returns
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.eth_return_period)]
        sol_returns = [ctx.features.get("sol_return_pct", 0.0) for _ in range(self.sol_return_period)]

        # Check if features are available (they should be due to required_features)
        if not eth_returns or not sol_returns:
            return None

        recent_eth_return = eth_returns[-1]
        recent_sol_return = sol_returns[-1]

        # Determine ETH's decisive move and direction
        eth_decisive_long = recent_eth_return > self.eth_decisive_threshold
        eth_decisive_short = recent_eth_return < -self.eth_decisive_threshold

        current_eth_direction = 0
        if eth_decisive_long:
            current_eth_direction = 1
        elif eth_decisive_short:
            current_eth_direction = -1

        # Determine SOL's lag
        sol_lagging_long = recent_sol_return < self.sol_lag_threshold and recent_sol_return >= 0
        sol_lagging_short = recent_sol_return > -self.sol_lag_threshold and recent_sol_return <= 0

        signal_direction = None
        confidence = 0.0
        reason = "no_signal"
        metadata = {
            "eth_return_pct": recent_eth_return,
            "sol_return_pct": recent_sol_return,
            "ema": ema,
            "current_price": current_price,
        }

        # Entry logic: ETH makes a decisive move, SOL lags, and not already in a position
        if not ctx.has_position():
            # Long condition
            if eth_decisive_long and sol_lagging_long and current_eth_direction == 1 and self.prev_eth_return_direction != 1:
                signal_direction = "long"
                confidence = 0.7
                reason = "eth_decisive_long_sol_lagging_beta_convergence"
                self.last_trade_bar = ctx.bar_index

            # Short condition
            elif eth_decisive_short and sol_lagging_short and current_eth_direction == -1 and self.prev_eth_return_direction != -1:
                signal_direction = "short"
                confidence = 0.7
                reason = "eth_decisive_short_sol_lagging_beta_convergence"
                self.last_trade_bar = ctx.bar_index

        # Exit logic: if in position and the relationship normalizes or reverses
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit if SOL has caught up or ETH reverses
                if recent_sol_return >= self.eth_decisive_threshold * 0.8 or recent_eth_return < -self.eth_decisive_threshold * 0.5:
                    signal_direction = "flat"
                    confidence = 0.6
                    reason = "long_exit_sol_caught_up_or_eth_reversed"
                    self.last_trade_bar = ctx.bar_index
            elif ctx.position_direction() == "short":
                # Exit if SOL has caught up or ETH reverses
                if recent_sol_return <= -self.eth_decisive_threshold * 0.8 or recent_eth_return > self.eth_decisive_threshold * 0.5:
                    signal_direction = "flat"
                    confidence = 0.6
                    reason = "short_exit_sol_caught_up_or_eth_reversed"
                    self.last_trade_bar = ctx.bar_index

        # Update previous direction for transition detection
        self.prev_eth_return_direction = current_eth_direction

        if signal_direction:
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata | {"reason": reason}
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to enforce cooldown period after any position close
        self.last_trade_bar = ctx.bar_index
        # Optional: Log position close details
        # print(f"Position closed: {position['direction']} at {position['exit_price']} for {position['return_bps']} bps return.")