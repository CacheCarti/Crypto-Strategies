from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthBetaConvergence(Strategy):
    METADATA = {
        "name": "SolEthBetaConvergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Average 4-hour hold
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 5
        self.slow_period = 20
        self.cooldown_bars = 6  # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.min_sol_move_pct = 0.5  # Minimum % move in SOL to consider it "decisive"
        self.max_eth_lag_pct = 0.2   # Maximum % ETH has already moved in the same direction
        self.min_sol_momentum_ratio = 1.5 # Ratio of fast EMA to slow EMA for SOL to confirm momentum

        # Previous values to detect transitions
        self.prev_sol_return_pct = 0.0
        self.prev_eth_return_pct = 0.0
        self.prev_sol_fast_ema = None
        self.prev_sol_slow_ema = None


    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.has_position():
            # Check for position normalization or hold time
            if ctx.position["bars_held"] > self.METADATA["declared_hold_seconds"] / 3600: # Convert to bars
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "time_stop", "bars_held": ctx.position["bars_held"]})
            return None # Hold existing position

        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None # Still in cooldown

        sol_returns = [ctx.features.get("sol_return_pct", 0.0) for _ in range(self.slow_period + 1)]
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.slow_period + 1)]

        # Calculate EMAs for SOL returns
        sol_fast_ema = self._ema(sol_returns, self.fast_period)
        sol_slow_ema = self._ema(sol_returns, self.slow_period)

        current_sol_return_pct = ctx.features.get("sol_return_pct", 0.0)
        current_eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

        signal_direction = None
        reason = ""
        confidence = 0.0

        # Check for decisive SOL move and ETH lag
        # Long condition: SOL moves up decisively, ETH lags, and SOL momentum is bullish
        if (sol_fast_ema is not None and sol_slow_ema is not None and
            sol_fast_ema > 0 and sol_fast_ema > sol_slow_ema * self.min_sol_momentum_ratio and
            current_sol_return_pct > self.min_sol_move_pct and
            current_eth_return_pct < current_sol_return_pct * self.max_eth_lag_pct and
            not ctx.has_position() # Only enter if no position
        ):
            # Ensure it's a fresh decisive move, not just a continuation
            # Check if SOL fast EMA just crossed above slow EMA or is maintaining strong upward momentum
            if (self.prev_sol_fast_ema is not None and self.prev_sol_slow_ema is not None and
                self.prev_sol_fast_ema <= self.prev_sol_slow_ema and sol_fast_ema > sol_slow_ema): # Crossover
                signal_direction = "long"
                reason = "sol_eth_beta_convergence_long_crossover"
                confidence = 0.75
            elif (self.prev_sol_fast_ema is not None and sol_fast_ema > self.prev_sol_fast_ema and # Maintaining momentum
                  sol_fast_ema > sol_slow_ema and self.prev_sol_fast_ema > self.prev_sol_slow_ema):
                signal_direction = "long"
                reason = "sol_eth_beta_convergence_long_momentum"
                confidence = 0.65

        # Short condition: SOL moves down decisively, ETH lags, and SOL momentum is bearish
        elif (sol_fast_ema is not None and sol_slow_ema is not None and
              sol_fast_ema < 0 and sol_fast_ema < sol_slow_ema * (1/self.min_sol_momentum_ratio) and # Inverse ratio for bearish
              current_sol_return_pct < -self.min_sol_move_pct and
              current_eth_return_pct > current_sol_return_pct * self.max_eth_lag_pct and
              not ctx.has_position() # Only enter if no position
        ):
            # Ensure it's a fresh decisive move
            if (self.prev_sol_fast_ema is not None and self.prev_sol_slow_ema is not None and
                self.prev_sol_fast_ema >= self.prev_sol_slow_ema and sol_fast_ema < sol_slow_ema): # Crossover
                signal_direction = "short"
                reason = "sol_eth_beta_convergence_short_crossover"
                confidence = 0.75
            elif (self.prev_sol_fast_ema is not None and sol_fast_ema < self.prev_sol_fast_ema and # Maintaining momentum
                  sol_fast_ema < sol_slow_ema and self.prev_sol_fast_ema < self.prev_sol_slow_ema):
                signal_direction = "short"
                reason = "sol_eth_beta_convergence_short_momentum"
                confidence = 0.65

        # Update previous values for next bar's transition detection
        self.prev_sol_return_pct = current_sol_return_pct
        self.prev_eth_return_pct = current_eth_return_pct
        self.prev_sol_fast_ema = sol_fast_ema
        self.prev_sol_slow_ema = sol_slow_ema

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_sol_return_pct": current_sol_return_pct,
                    "current_eth_return_pct": current_eth_return_pct,
                    "sol_fast_ema": sol_fast_ema,
                    "sol_slow_ema": sol_slow_ema,
                    "min_sol_move_pct": self.min_sol_move_pct,
                    "max_eth_lag_pct": self.max_eth_lag_pct,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close
        self.last_trade_bar = ctx.bar_index
        # Log for adaptive sizing if needed later
        # print(f"Position closed: {position['direction']} return {position['return_bps']:.2f} bps, held for {position['bars_held']} bars")