from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BetaConvergenceBTC(Strategy):
    METADATA = {
        "name": "BetaConvergenceBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
        "required_features": ["sol_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 12 # For recent returns
        self.divergence_threshold = 0.005 # 0.5% divergence
        self.convergence_threshold = 0.001 # 0.1% convergence to exit
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        if len(closes) < self.lookback_period + 1:
            return None

        current_btc_return = ctx.features.get("btc_return_pct", 0.0) / 100.0 # Convert % to decimal
        current_sol_return = ctx.features.get("sol_return_pct", 0.0) / 100.0 # Convert % to decimal

        # Require a fresh, decisive move from SOL (leader)
        # Use simple recent return for decisiveness
        recent_sol_closes = ctx.closes(self.lookback_period)
        if len(recent_sol_closes) < self.lookback_period:
            return None

        # Calculate recent BTC price change as a proxy for its "lag"
        btc_price_change = (closes[-1] - closes[-2]) / closes[-2] if len(closes) >= 2 else 0.0

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit logic
        if ctx.has_position():
            pos = ctx.position
            # Check for convergence or time stop
            if pos["direction"] == "long":
                # If BTC has caught up or surpassed SOL's recent move
                if (current_btc_return - current_sol_return) >= -self.convergence_threshold:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "long_convergence_exit",
                        "current_btc_return": current_btc_return,
                        "current_sol_return": current_sol_return,
                        "divergence": current_btc_return - current_sol_return,
                    })
            elif pos["direction"] == "short":
                # If BTC has caught up or surpassed SOL's recent move
                if (current_btc_return - current_sol_return) <= self.convergence_threshold:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "short_convergence_exit",
                        "current_btc_return": current_btc_return,
                        "current_sol_return": current_sol_return,
                        "divergence": current_btc_return - current_sol_return,
                    })
            return None # Hold position if no exit condition met

        # Entry logic
        signal = None
        confidence = 0.7

        # Long: SOL moves up decisively, BTC lags
        if current_sol_return > self.divergence_threshold and \
           (current_sol_return - current_btc_return) > self.divergence_threshold:
            signal = "long"
            reason = "sol_up_btc_lag_beta_convergence"

        # Short: SOL moves down decisively, BTC lags
        elif current_sol_return < -self.divergence_threshold and \
             (current_sol_return - current_btc_return) < -self.divergence_threshold:
            signal = "short"
            reason = "sol_down_btc_lag_beta_convergence"

        if signal:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_sol_return": current_sol_return,
                    "current_btc_return": current_btc_return,
                    "divergence": current_sol_return - current_btc_return,
                    "threshold": self.divergence_threshold,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log for debugging or future adaptive logic
        pass