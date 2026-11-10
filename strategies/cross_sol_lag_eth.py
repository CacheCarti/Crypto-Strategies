from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthBetaConvergence(Strategy):
    METADATA = {
        "name": "SolEthBetaConvergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60,
        "required_features": ["sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24 # 24 hours for return comparison
        self.divergence_threshold = 0.015 # 1.5% divergence
        self.cooldown_bars = 12 # 12 hours cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars * 2 # Initialize to allow first trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        if len(closes) < self.lookback_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        sol_return_pct = ctx.features.get("sol_return_pct", 0.0)
        eth_return_pct = (ctx.bar.close - closes[0]) / closes[0]

        # We are looking for a 'fresh decisive move' in SOL.
        # Let's define this as a significant 1-bar move in SOL that ETH has not matched.
        # We'll use the 1-hour return for SOL, and a slightly longer return for ETH to see if it's lagging.
        
        # Check if SOL has made a significant move in the last bar
        sol_recent_move = ctx.market.get("sol_return_pct", 0.0) # This is 1-bar return for SOL
        
        # Calculate ETH's recent move over the same short period
        eth_recent_move_1h = (ctx.bar.close - ctx.opens(1)[0]) / ctx.opens(1)[0] if len(ctx.opens(1)) > 0 else 0.0


        signal_direction = None
        confidence = 0.0
        reason = ""
        
        # Long condition: SOL pumps, ETH lags
        if sol_recent_move > self.divergence_threshold and eth_recent_move_1h < self.divergence_threshold / 2:
            # Confirm with ETH price action: current bar close should be above previous close
            if ctx.bar.close > ctx.closes(2)[0]:
                signal_direction = "long"
                confidence = min(0.7, abs(sol_recent_move) * 20) # Scale confidence by divergence magnitude
                reason = "sol_pump_eth_lag_bullish_close"
        
        # Short condition: SOL dumps, ETH lags
        elif sol_recent_move < -self.divergence_threshold and eth_recent_move_1h > -self.divergence_threshold / 2:
            # Confirm with ETH price action: current bar close should be below previous close
            if ctx.bar.close < ctx.closes(2)[0]:
                signal_direction = "short"
                confidence = min(0.7, abs(sol_recent_move) * 20)
                reason = "sol_dump_eth_lag_bearish_close"

        if ctx.has_position():
            # Exit condition: if the divergence has converged or reversed
            # We check if ETH has 'caught up' or SOL has reversed course
            if ctx.position_direction() == "long":
                if eth_recent_move_1h > sol_recent_move * 0.8 or sol_recent_move < 0: # ETH caught up or SOL reversed
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_divergence_converged_or_sol_reversed", "sol_move": sol_recent_move, "eth_move": eth_recent_move_1h})
            elif ctx.position_direction() == "short":
                if eth_recent_move_1h < sol_recent_move * 0.8 or sol_recent_move > 0: # ETH caught up or SOL reversed
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_divergence_converged_or_sol_reversed", "sol_move": sol_recent_move, "eth_move": eth_recent_move_1h})
            return None # Hold position if no exit condition met

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "sol_1h_move": sol_recent_move,
                    "eth_1h_move": eth_recent_move_1h,
                    "divergence_threshold": self.divergence_threshold
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We can log or adapt here if needed. For now, just track last trade.
        pass