from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BetaConvergenceEthSol(Strategy):
    METADATA = {
        "name": "ETH-SOL Beta Convergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2-hour hold
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.slow_period = 30
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        sol_returns = [ctx.features.get("sol_return_pct", 0.0) for _ in range(self.fast_period)]
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.fast_period)]

        # Ensure we have enough data for fresh returns
        if len(sol_returns) < self.fast_period or len(eth_returns) < self.fast_period:
            return None

        current_sol_return = ctx.features.get("sol_return_pct", 0.0)
        current_eth_return = ctx.features.get("eth_return_pct", 0.0)

        # Calculate recent average returns
        avg_sol_return_fast = sum(sol_returns[-self.fast_period:]) / self.fast_period
        avg_eth_return_fast = sum(eth_returns[-self.fast_period:]) / self.fast_period

        # Calculate longer-term average returns for context
        avg_sol_return_slow = sum(sol_returns[-self.slow_period:]) / self.slow_period
        avg_eth_return_slow = sum(eth_returns[-self.slow_period:]) / self.slow_period

        # Get current price context
        current_price = ctx.bar.close
        fast_ema = self._ema(closes, self.fast_period)
        slow_ema = self._ema(closes, self.slow_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Volatility filter: only trade in normal or volatile regimes
        if ctx.regime == "calm" or ctx.market.get("crisis_score", 0.0) > 0.5:
            return None

        signal_direction = None
        confidence = 0.0
        reason = ""
        metadata = {}

        # Long condition: SOL makes a strong positive fresh move, ETH lags, and ETH is in a short-term dip relative to its EMA
        if current_sol_return > 0.5 and \
           avg_sol_return_fast > 0.3 and \
           current_eth_return < 0.1 and \
           avg_eth_return_fast < avg_sol_return_fast and \
           current_price < fast_ema and \
           fast_ema > slow_ema: # Ensure ETH is in an underlying uptrend
            
            signal_direction = "long"
            confidence = min(1.0, (current_sol_return - current_eth_return) / 1.0) # Scale confidence by divergence
            reason = "sol_leading_eth_lagging_bullish_convergence"
            metadata = {
                "sol_return_pct": current_sol_return,
                "eth_return_pct": current_eth_return,
                "avg_sol_return_fast": avg_sol_return_fast,
                "avg_eth_return_fast": avg_eth_return_fast,
                "current_price": current_price,
                "fast_ema": fast_ema,
                "slow_ema": slow_ema,
            }

        # Short condition: SOL makes a strong negative fresh move, ETH lags, and ETH is in a short-term bounce relative to its EMA
        elif current_sol_return < -0.5 and \
             avg_sol_return_fast < -0.3 and \
             current_eth_return > -0.1 and \
             avg_eth_return_fast > avg_sol_return_fast and \
             current_price > fast_ema and \
             fast_ema < slow_ema: # Ensure ETH is in an underlying downtrend
            
            signal_direction = "short"
            confidence = min(1.0, (current_eth_return - current_sol_return) / 1.0) # Scale confidence by divergence
            reason = "sol_leading_eth_lagging_bearish_convergence"
            metadata = {
                "sol_return_pct": current_sol_return,
                "eth_return_pct": current_eth_return,
                "avg_sol_return_fast": avg_sol_return_fast,
                "avg_eth_return_fast": avg_eth_return_fast,
                "current_price": current_price,
                "fast_ema": fast_ema,
                "slow_ema": slow_ema,
            }

        # Position management
        if ctx.has_position():
            if signal_direction is not None and signal_direction != ctx.position_direction():
                # Flip position if opposite signal is strong
                self.last_trade_bar = ctx.bar_index
                metadata["exit_reason"] = "flip_signal"
                return ctx.signal(signal_direction, confidence=confidence, metadata=metadata)
            
            # Exit if the convergence condition normalizes or reverses
            if ctx.position_direction() == "long":
                if current_eth_return > current_sol_return * 0.5 or \
                   current_price < slow_ema: # Price breaks below slow EMA
                    self.last_trade_bar = ctx.bar_index
                    metadata["exit_reason"] = "convergence_normalized_or_breakdown"
                    return ctx.signal("flat", confidence=0.7, metadata=metadata)
            elif ctx.position_direction() == "short":
                if current_eth_return < current_sol_return * 0.5 or \
                   current_price > slow_ema: # Price breaks above slow EMA
                    self.last_trade_bar = ctx.bar_index
                    metadata["exit_reason"] = "convergence_normalized_or_breakout"
                    return ctx.signal("flat", confidence=0.7, metadata=metadata)
            
            # Hold if no new signal or exit condition
            return None # Hold current position

        elif signal_direction is not None and confidence > 0.6: # Only enter if no position and high confidence
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata=metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt strategy based on position outcome
        # self.last_trade_bar = ctx.bar_index # Cooldown handled in on_bar for consistency
        pass