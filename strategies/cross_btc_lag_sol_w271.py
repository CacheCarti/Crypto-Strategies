from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BetaConvergenceSOL(Strategy):
    METADATA = {
        "name": "Beta Convergence SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Aim for 4-hour holds
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_window = 5 # Bars to look for BTC's decisive move
        self.sol_lag_window = 3 # Bars to check SOL's underperformance
        self.min_btc_move_pct = 0.5 # Minimum BTC move in percent (e.g., 0.5% in 5 bars)
        self.max_sol_lag_pct = 0.2 # Maximum SOL move in percent during its lag window
        self.cooldown_bars = 10 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown period after a trade
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        closes = ctx.closes(self.btc_window + 1)
        if len(closes) < self.btc_window + 1:
            return None

        current_price = ctx.bar.close

        btc_returns = ctx.features.get("btc_return_pct", 0.0)
        sol_returns = ctx.features.get("sol_return_pct", 0.0)

        # Check for decisive BTC move (e.g., 5-bar return)
        btc_history = []
        for i in range(self.btc_window):
            btc_history.append(ctx.features.get("btc_return_pct", 0.0, offset=i))

        # Check for SOL's relative underperformance/overperformance
        sol_recent_closes = ctx.closes(self.sol_lag_window + 1)
        if len(sol_recent_closes) < self.sol_lag_window + 1:
            return None
        sol_recent_move_pct = ((sol_recent_closes[-1] - sol_recent_closes[0]) / sol_recent_closes[0]) * 100.0

        # Calculate BTC's move over the window
        btc_past_move_pct = sum(btc_history[-self.btc_window:])

        # Long condition: BTC makes a fresh strong move up, SOL has not caught up
        if not ctx.has_position() and btc_past_move_pct >= self.min_btc_move_pct and sol_recent_move_pct < self.max_sol_lag_pct:
            confidence = min(1.0, btc_past_move_pct / (self.min_btc_move_pct * 2)) # Scale confidence
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "btc_beta_convergence_long",
                    "btc_past_move_pct": btc_past_move_pct,
                    "sol_recent_move_pct": sol_recent_move_pct,
                    "current_price": current_price,
                    "btc_return_current": btc_returns,
                    "sol_return_current": sol_returns,
                }
            )

        # Short condition: BTC makes a fresh strong move down, SOL has not caught up (or even moved up)
        if not ctx.has_position() and btc_past_move_pct <= -self.min_btc_move_pct and sol_recent_move_pct > -self.max_sol_lag_pct:
            confidence = min(1.0, abs(btc_past_move_pct) / (self.min_btc_move_pct * 2)) # Scale confidence
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                metadata={
                    "reason": "btc_beta_convergence_short",
                    "btc_past_move_pct": btc_past_move_pct,
                    "sol_recent_move_pct": sol_recent_move_pct,
                    "current_price": current_price,
                    "btc_return_current": btc_returns,
                    "sol_return_current": sol_returns,
                }
            )

        # Exit condition: If a position is open and SOL's recent move has now caught up or exceeded BTC's direction
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # If SOL has now moved significantly in the positive direction
                if sol_recent_move_pct > self.min_btc_move_pct / 2: # Exit if SOL has moved half of BTC's threshold
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "sol_caught_up_long", "sol_recent_move_pct": sol_recent_move_pct, "current_price": current_price})
            elif ctx.position_direction() == "short":
                # If SOL has now moved significantly in the negative direction
                if sol_recent_move_pct < -self.min_btc_move_pct / 2: # Exit if SOL has moved half of BTC's threshold
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "sol_caught_up_short", "sol_recent_move_pct": sol_recent_move_pct, "current_price": current_price})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        # Optional: Adaptive logic based on position outcome
        # For example, if a trade was very profitable, increase confidence for next few trades
        # Or if it was a loss, reduce position size or increase cooldown
        pass