from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BetaConvergenceEthSol(Strategy):
    METADATA = {
        "name": "Beta Convergence ETH-SOL",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Intended 2-hour hold
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.leader_lookback = 3 # Bars to check for leader's move
        self.follower_lookback = 1 # Bars to check for follower's lag
        self.min_leader_move_pct = 0.5 # Minimum % move for SOL to be considered decisive
        self.max_follower_lag_pct = 0.1 # Maximum % move for ETH to be considered lagging
        self.cooldown_bars = 5 # Bars to wait after a trade before entering again
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        sol_returns = [ctx.features.get("sol_return_pct", 0.0) for _ in range(self.leader_lookback)]
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.follower_lookback)]

        # Check for sufficient data
        if not sol_returns or not eth_returns:
            return None

        # Calculate recent SOL move
        recent_sol_move = sum(sol_returns) # Sum of recent SOL returns
        
        # Calculate recent ETH move
        recent_eth_move = sum(eth_returns) # Sum of recent ETH returns

        signal_direction = None
        reason = ""
        confidence = 0.0

        # Condition 1: SOL makes a strong positive move, ETH lags
        if recent_sol_move > self.min_leader_move_pct and recent_eth_move < self.max_follower_lag_pct:
            signal_direction = "long"
            reason = "sol_leader_eth_lagging_bullish"
            confidence = min(1.0, (recent_sol_move - recent_eth_move) / (self.min_leader_move_pct * 2)) # Scale confidence based on divergence

        # Condition 2: SOL makes a strong negative move, ETH lags (or doesn't fall as much)
        elif recent_sol_move < -self.min_leader_move_pct and recent_eth_move > -self.max_follower_lag_pct:
            signal_direction = "short"
            reason = "sol_leader_eth_lagging_bearish"
            confidence = min(1.0, (abs(recent_sol_move) - abs(recent_eth_move)) / (self.min_leader_move_pct * 2)) # Scale confidence

        # If a position is open, check for exit conditions
        if ctx.has_position():
            position = ctx.position
            current_price = ctx.bar.close
            entry_price = position["entry_price"]
            direction = position["direction"]

            # Exit if the convergence seems to have occurred (or momentum faded)
            # We use a simple moving average crossover for exit logic
            period_fast = 5
            period_slow = 10
            
            closes = ctx.closes(max(period_fast, period_slow) + 1)
            if len(closes) < max(period_fast, period_slow) + 1:
                return None # Not enough data for exit indicators

            sma_fast = self._sma(closes, period_fast)
            sma_slow = self._sma(closes, period_slow)

            if sma_fast is None or sma_slow is None:
                return None

            if direction == "long":
                # Exit long if fast SMA crosses below slow SMA (bearish convergence)
                if sma_fast < sma_slow and closes[-1] < closes[-2]: # Additional check for price confirmation
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "sma_bearish_cross_exit_long",
                        "sma_fast": sma_fast,
                        "sma_slow": sma_slow,
                        "current_price": current_price,
                        "entry_price": entry_price
                    })
            elif direction == "short":
                # Exit short if fast SMA crosses above slow SMA (bullish convergence)
                if sma_fast > sma_slow and closes[-1] > closes[-2]: # Additional check for price confirmation
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "sma_bullish_cross_exit_short",
                        "sma_fast": sma_fast,
                        "sma_slow": sma_slow,
                        "current_price": current_price,
                        "entry_price": entry_price
                    })
            return None # Hold position if no exit signal

        # No position, consider new entry
        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "sol_move_pct": recent_sol_move,
                    "eth_move_pct": recent_eth_move,
                    "current_price": ctx.bar.close,
                    "bar_index": ctx.bar_index
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis
        pass