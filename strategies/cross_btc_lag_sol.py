from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcLagSolBeta(Strategy):
    METADATA = {
        "name": "BTC Lag SOL Beta",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, aiming for ~1-2 bars hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_window = 5 # bars for BTC return check
        self.sol_window = 2 # bars for SOL return check
        self.cooldown_bars = 5 # cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Thresholds for BTC decisive move
        self.btc_decisive_move_threshold_long = 0.005 # 0.5% positive move
        self.btc_decisive_move_threshold_short = -0.005 # 0.5% negative move

        # Threshold for SOL underperformance/overperformance
        self.sol_lag_threshold = 0.001 # 0.1% difference

        # EMA for trend filtering (optional, for more robustness)
        self.ema_period = 20

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

        if ctx.has_position():
            # Check if SOL has caught up or reversed significantly
            # We can use the current bar's SOL return to check for normalization
            sol_returns = ctx.features.get("sol_return_pct", 0.0)
            if ctx.position_direction() == "long":
                # If we are long and SOL has significantly caught up, consider exiting
                if sol_returns > 0.003: # 0.3% positive return on current bar
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "sol_catch_up_exit_long", "sol_return_pct": sol_returns})
                # If BTC returns turn negative while we are long, consider exiting
                if ctx.features.get("btc_return_pct", 0.0) < -0.001:
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "btc_reversal_exit_long", "btc_return_pct": ctx.features.get("btc_return_pct", 0.0)})
            elif ctx.position_direction() == "short":
                # If we are short and SOL has significantly dropped, consider exiting
                if sol_returns < -0.003: # 0.3% negative return on current bar
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "sol_catch_up_exit_short", "sol_return_pct": sol_returns})
                # If BTC returns turn positive while we are short, consider exiting
                if ctx.features.get("btc_return_pct", 0.0) > 0.001:
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "btc_reversal_exit_short", "btc_return_pct": ctx.features.get("btc_return_pct", 0.0)})
            return None # Hold position if no exit condition met

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Ensure we have enough data for lookback
        if len(ctx.closes(self.ema_period)) < self.ema_period:
            return None

        # Calculate recent BTC and SOL returns
        btc_returns_window = [ctx.features.get("btc_return_pct", 0.0, i) for i in range(self.btc_window)]
        sol_returns_window = [ctx.features.get("sol_return_pct", 0.0, i) for i in range(self.sol_window)]

        # Check for valid returns data (should usually be available)
        if not btc_returns_window or not sol_returns_window:
            return None

        recent_btc_return = sum(btc_returns_window) / self.btc_window
        recent_sol_return = sum(sol_returns_window) / self.sol_window

        # Get EMA for trend filtering
        current_ema = self._ema(ctx.closes(self.ema_period), self.ema_period)
        if current_ema is None:
            return None

        # Long Entry Condition: BTC decisively up, SOL lagging behind, and price above EMA
        if recent_btc_return > self.btc_decisive_move_threshold_long and \
           (recent_btc_return - recent_sol_return) > self.sol_lag_threshold and \
           ctx.bar.close > current_ema:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.7,
                metadata={
                    "reason": "btc_lag_sol_beta_long",
                    "btc_recent_return": recent_btc_return,
                    "sol_recent_return": recent_sol_return,
                    "sol_lag_bps": (recent_btc_return - recent_sol_return) * 10000,
                    "close": ctx.bar.close,
                    "ema_period": self.ema_period,
                    "ema_value": current_ema
                }
            )

        # Short Entry Condition: BTC decisively down, SOL lagging behind (less down), and price below EMA
        elif recent_btc_return < self.btc_decisive_move_threshold_short and \
             (recent_btc_return - recent_sol_return) < -self.sol_lag_threshold and \
             ctx.bar.close < current_ema:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.7,
                metadata={
                    "reason": "btc_lag_sol_beta_short",
                    "btc_recent_return": recent_btc_return,
                    "sol_recent_return": recent_sol_return,
                    "sol_lag_bps": (recent_btc_return - recent_sol_return) * 10000,
                    "close": ctx.bar.close,
                    "ema_period": self.ema_period,
                    "ema_value": current_ema
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust parameters
        pass