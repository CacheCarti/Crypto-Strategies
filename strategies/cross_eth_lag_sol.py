from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthSolLagConvergence(Strategy):
    METADATA = {
        "name": "EthSolLagConvergence",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.eth_return_period = 2  # Lookback for fresh ETH return
        self.sol_return_period = 2  # Lookback for fresh SOL return
        self.divergence_threshold = 0.005 # 0.5% divergence
        self.entry_cooldown_bars = 5 # Cooldown after an entry
        self.last_entry_bar = -self.entry_cooldown_bars
        self.exit_cooldown_bars = 3 # Cooldown after an exit
        self.last_exit_bar = -self.exit_cooldown_bars
        self.ema_period = 20 # For trend filtering

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_entry_bar < self.entry_cooldown_bars:
            return None
        if ctx.bar_index - self.last_exit_bar < self.exit_cooldown_bars:
            return None

        eth_returns = ctx.features.get("eth_return_pct", 0.0)
        sol_returns = ctx.features.get("sol_return_pct", 0.0)

        # Get recent ETH and SOL returns
        # eth_return_pct and sol_return_pct are already per-bar, representing 24h return.
        # To get "fresh" returns, we look at the change in this 24h return over the last few bars.
        # This is a proxy for recent price action.
        past_eth_returns = [ctx.features.get_past("eth_return_pct", i, 0.0) for i in range(self.eth_return_period + 1)]
        past_sol_returns = [ctx.features.get_past("sol_return_pct", i, 0.0) for i in range(self.sol_return_period + 1)]

        if any(r is None for r in past_eth_returns) or any(r is None for r in past_sol_returns):
            return None

        # Calculate recent change in 24h return to detect "fresh decisive move"
        recent_eth_move = past_eth_returns[-1] - past_eth_returns[0] # Current 24h return vs. N bars ago
        recent_sol_move = past_sol_returns[-1] - past_sol_returns[0]

        # Calculate EMA for trend filtering on SOL
        sol_ema = self._ema(closes, self.ema_period)
        if sol_ema is None:
            return None

        # Determine if ETH has made a "fresh decisive move"
        # And if SOL has not yet "caught up"
        
        signal_direction = None
        confidence = 0.0
        reason = ""
        
        # Long condition: ETH moves up decisively, SOL lags, and SOL is above its EMA (uptrend)
        if recent_eth_move > self.divergence_threshold and \
           recent_sol_move < self.divergence_threshold / 2 and \
           eth_returns > sol_returns and \
           ctx.bar.close > sol_ema:
            
            if not ctx.has_position() or ctx.position_direction() == "short":
                signal_direction = "long"
                confidence = min(1.0, abs(recent_eth_move - recent_sol_move) * 100) # Scale confidence by divergence
                reason = "eth_bull_move_sol_lagging_ema_confirm"

        # Short condition: ETH moves down decisively, SOL lags, and SOL is below its EMA (downtrend)
        elif recent_eth_move < -self.divergence_threshold and \
             recent_sol_move > -self.divergence_threshold / 2 and \
             eth_returns < sol_returns and \
             ctx.bar.close < sol_ema:

            if not ctx.has_position() or ctx.position_direction() == "long":
                signal_direction = "short"
                confidence = min(1.0, abs(recent_eth_move - recent_sol_move) * 100)
                reason = "eth_bear_move_sol_lagging_ema_confirm"

        # Exit logic: if we have a position and the relationship normalizes or reverses
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit if ETH starts to underperform SOL or SOL catches up too much
                if eth_returns < sol_returns or recent_sol_move > recent_eth_move * 0.75: # SOL caught up 75%
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_exit_sol_converged_or_eth_weakened",
                        "eth_return_pct": eth_returns,
                        "sol_return_pct": sol_returns,
                        "recent_eth_move": recent_eth_move,
                        "recent_sol_move": recent_sol_move
                    })
            elif ctx.position_direction() == "short":
                # Exit if ETH starts to outperform SOL or SOL catches up too much
                if eth_returns > sol_returns or recent_sol_move < recent_eth_move * 0.75: # SOL caught up 75%
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_exit_sol_converged_or_eth_strengthened",
                        "eth_return_pct": eth_returns,
                        "sol_return_pct": sol_returns,
                        "recent_eth_move": recent_eth_move,
                        "recent_sol_move": recent_sol_move
                    })

        if signal_direction and confidence > 0.3: # Only signal if sufficient confidence
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "eth_return_pct": eth_returns,
                    "sol_return_pct": sol_returns,
                    "recent_eth_move": recent_eth_move,
                    "recent_sol_move": recent_sol_move,
                    "sol_ema": sol_ema,
                    "current_price": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after a position closes for any reason
        self.last_exit_bar = ctx.bar_index