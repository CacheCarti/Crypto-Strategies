from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BetaLagSOL(Strategy):
    METADATA = {
        "name": "Beta Lag SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Aim for 4-hour holds
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_leader_move_pct = 0.5 # BTC/ETH must move at least this much
        self.max_lag_ratio = 0.5      # SOL's move must be at most this ratio of leader's move
        self.cooldown_bars = 12       # Cooldown for 12 bars (12 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown period after a previous trade
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

        # Get SOL's return over the last bar
        sol_returns = [(c - p) / p * 100 for c, p in zip(ctx.closes(2)[1:], ctx.closes(2)[:-1])]
        sol_return_pct = sol_returns[-1] if sol_returns else 0.0

        leader_direction = None
        leader_move_magnitude = 0.0

        # Check for decisive leader movement (both BTC and ETH moving in the same direction)
        if btc_return_pct > self.min_leader_move_pct and eth_return_pct > self.min_leader_move_pct:
            leader_direction = "long"
            leader_move_magnitude = (btc_return_pct + eth_return_pct) / 2
        elif btc_return_pct < -self.min_leader_move_pct and eth_return_pct < -self.min_leader_move_pct:
            leader_direction = "short"
            leader_move_magnitude = abs((btc_return_pct + eth_return_pct) / 2)

        if leader_direction is None or leader_move_magnitude < self.min_leader_move_pct:
            return None # No decisive leader movement

        # Check for SOL's lag
        sol_move_magnitude = abs(sol_return_pct)

        if sol_move_magnitude < leader_move_magnitude * self.max_lag_ratio:
            # SOL is lagging, enter in leader's direction
            if leader_direction == "long":
                if not ctx.has_position() or ctx.position_direction() == "short":
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "beta_lag_long",
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "sol_return_pct": sol_return_pct,
                            "leader_move_magnitude": leader_move_magnitude,
                        }
                    )
            elif leader_direction == "short":
                if not ctx.has_position() or ctx.position_direction() == "long":
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "beta_lag_short",
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "sol_return_pct": sol_return_pct,
                            "leader_move_magnitude": leader_move_magnitude,
                        }
                    )
        
        # Exit logic: if SOL has caught up or reversed significantly
        if ctx.has_position():
            position_direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]
            current_price = ctx.bar.close
            
            # Check if lag has closed
            if position_direction == "long":
                # If SOL's current move is now proportional or opposite
                current_move_since_entry = (current_price - entry_price) / entry_price * 100
                if current_move_since_entry >= leader_move_magnitude * 0.8 or current_move_since_entry < -self.min_leader_move_pct: # 80% catch-up or significant reversal
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.5, metadata={
                        "reason": "beta_lag_closed_long",
                        "current_sol_move_pct": current_move_since_entry,
                        "leader_move_magnitude": leader_move_magnitude,
                    })
            elif position_direction == "short":
                # If SOL's current move is now proportional or opposite
                current_move_since_entry = (entry_price - current_price) / entry_price * 100
                if current_move_since_entry >= leader_move_magnitude * 0.8 or current_move_since_entry < -self.min_leader_move_pct: # 80% catch-up or significant reversal
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.5, metadata={
                        "reason": "beta_lag_closed_short",
                        "current_sol_move_pct": current_move_since_entry,
                        "leader_move_magnitude": leader_move_magnitude,
                    })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close
        self.last_trade_bar = ctx.bar_index
        pass