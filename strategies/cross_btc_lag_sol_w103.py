from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BetaConvergence(Strategy):
    METADATA = {
        "name": "Beta Convergence",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Roughly 4 hours
        "warmup_bars": 30,
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_lookback_period = 12 # 12 hours for BTC
        self.sol_lookback_period = 3 # 3 hours for SOL
        self.divergence_threshold = 0.005 # 0.5% divergence
        self.recent_move_threshold = 0.01 # BTC moved at least 1%
        self.cooldown_bars = 6 # Cooldown for 6 bars (6 hours)
        self.last_signal_bar = -self.cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        current_btc_return = ctx.features.get("btc_return_pct", 0.0)
        current_sol_return = ctx.features.get("sol_return_pct", 0.0)

        # Ensure we have enough history for the lookback periods
        if len(ctx.closes(self.btc_lookback_period + 1)) <= self.btc_lookback_period or \
           len(ctx.closes(self.sol_lookback_period + 1)) <= self.sol_lookback_period:
            return None

        # Calculate recent BTC performance
        btc_closes = ctx.closes(self.btc_lookback_period + 1)
        btc_recent_change = (btc_closes[-1] - btc_closes[0]) / btc_closes[0] if btc_closes[0] != 0 else 0.0

        # Calculate recent SOL performance
        sol_closes = ctx.closes(self.sol_lookback_period + 1)
        sol_recent_change = (sol_closes[-1] - sol_closes[0]) / sol_closes[0] if sol_closes[0] != 0 else 0.0

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # If a position is open, check for exit conditions
        if ctx.has_position():
            # Exit if the divergence has normalized or reversed
            if ctx.position_direction() == "long":
                if sol_recent_change > btc_recent_change - self.divergence_threshold / 2: # SOL caught up or overshot
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_beta_convergence_normalized",
                        "btc_recent_change": btc_recent_change,
                        "sol_recent_change": sol_recent_change,
                        "current_sol_price": ctx.bar.close
                    })
            elif ctx.position_direction() == "short":
                if sol_recent_change < btc_recent_change + self.divergence_threshold / 2: # SOL caught up or overshot
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_beta_convergence_normalized",
                        "btc_recent_change": btc_recent_change,
                        "sol_recent_change": sol_recent_change,
                        "current_sol_price": ctx.bar.close
                    })
            return None # Hold current position

        # Entry logic: BTC makes a decisive move, SOL hasn't caught up
        confidence = 0.0

        # Long scenario: BTC significantly up, SOL lagging
        if btc_recent_change > self.recent_move_threshold and \
           btc_recent_change - sol_recent_change > self.divergence_threshold:
            confidence = min(0.9, (btc_recent_change - sol_recent_change) * 50) # Scale confidence by divergence
            self.last_signal_bar = ctx.bar_index
            return ctx.signal("long", confidence=confidence, metadata={
                "reason": "btc_up_sol_lagging_beta_convergence_long",
                "btc_recent_change": btc_recent_change,
                "sol_recent_change": sol_recent_change,
                "divergence": btc_recent_change - sol_recent_change,
                "current_sol_price": ctx.bar.close
            })

        # Short scenario: BTC significantly down, SOL lagging (not falling as much)
        elif btc_recent_change < -self.recent_move_threshold and \
             sol_recent_change - btc_recent_change > self.divergence_threshold:
            confidence = min(0.9, (sol_recent_change - btc_recent_change) * 50) # Scale confidence by divergence
            self.last_signal_bar = ctx.bar_index
            return ctx.signal("short", confidence=confidence, metadata={
                "reason": "btc_down_sol_lagging_beta_convergence_short",
                "btc_recent_change": btc_recent_change,
                "sol_recent_change": sol_recent_change,
                "divergence": sol_recent_change - btc_recent_change,
                "current_sol_price": ctx.bar.close
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust future behavior based on performance
        pass