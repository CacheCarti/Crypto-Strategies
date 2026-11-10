from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BetaConvergenceEth(Strategy):
    METADATA = {
        "name": "Beta Convergence ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours, allowing for convergence
        "warmup_bars": 30,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_lookback = 4
        self.eth_lookback = 2
        self.min_btc_move_pct = 0.5 # BTC must move at least 0.5%
        self.max_eth_lag_pct = 0.2 # ETH must have moved less than 0.2% in the same direction
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"] + max(self.btc_lookback, self.eth_lookback):
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.btc_lookback)]
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.eth_lookback)]

        # Get recent BTC returns (sum of last `btc_lookback` bars)
        recent_btc_move = sum(btc_returns[-self.btc_lookback:])
        
        # Get recent ETH returns (sum of last `eth_lookback` bars)
        recent_eth_move = sum(eth_returns[-self.eth_lookback:])

        signal_direction = None
        reason = ""
        confidence = 0.0

        # BTC makes a significant move up, but ETH hasn't caught up
        if recent_btc_move > self.min_btc_move_pct and recent_eth_move < self.max_eth_lag_pct:
            if not ctx.has_position():
                signal_direction = "long"
                confidence = min(1.0, (recent_btc_move - recent_eth_move) / (self.min_btc_move_pct * 2))
                reason = "btc_leading_eth_lagging_long"
            elif ctx.position_direction() == "short": # If short, flip to long
                signal_direction = "long"
                confidence = 0.8
                reason = "btc_leading_eth_lagging_long_flip"
        
        # BTC makes a significant move down, but ETH hasn't caught up
        elif recent_btc_move < -self.min_btc_move_pct and recent_eth_move > -self.max_eth_lag_pct:
            if not ctx.has_position():
                signal_direction = "short"
                confidence = min(1.0, (-recent_btc_move - (-recent_eth_move)) / (self.min_btc_move_pct * 2))
                reason = "btc_leading_eth_lagging_short"
            elif ctx.position_direction() == "long": # If long, flip to short
                signal_direction = "short"
                confidence = 0.8
                reason = "btc_leading_eth_lagging_short_flip"

        # Exit condition: if BTC and ETH have converged or diverged too much
        if ctx.has_position():
            # If ETH has now moved past BTC in the same direction, or significantly against
            if ctx.position_direction() == "long" and recent_eth_move >= recent_btc_move * 0.8: # ETH caught up
                signal_direction = "flat"
                reason = "beta_convergence_achieved_long"
            elif ctx.position_direction() == "short" and recent_eth_move <= recent_btc_move * 0.8: # ETH caught up
                signal_direction = "flat"
                reason = "beta_convergence_achieved_short"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "recent_btc_move_pct": recent_btc_move,
                    "recent_eth_move_pct": recent_eth_move,
                    "current_price": ctx.bar.close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally log or adapt based on position close
        pass