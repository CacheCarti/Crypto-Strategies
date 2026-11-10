from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetSolLagBtc(Strategy):
    METADATA = {
        "name": "CrossAssetSolLagBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.sol_return_period = 3 # Lookback for SOL return
        self.btc_return_period = 3 # Lookback for BTC return
        self.min_sol_move_pct = 0.5 # Minimum SOL move to trigger, in percent
        self.max_btc_lag_pct = 0.2 # Maximum BTC return during the SOL move, in percent

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

        sol_returns = [ctx.features.get("sol_return_pct", 0.0) for _ in range(self.sol_return_period)]
        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.btc_return_period)]

        # Ensure we have enough data for the lookback periods
        if len(sol_returns) < self.sol_return_period or len(btc_returns) < self.btc_return_period:
            return None

        current_sol_return_pct = ctx.features.get("sol_return_pct", 0.0)
        current_btc_return_pct = ctx.features.get("btc_return_pct", 0.0)

        # Calculate average returns over the recent period
        avg_sol_return_pct = sum(sol_returns) / len(sol_returns)
        avg_btc_return_pct = sum(btc_returns) / len(btc_returns)

        signal_direction = None
        confidence = 0.0
        reason = ""
        
        # Long condition: SOL makes a significant positive move, BTC lags behind
        if avg_sol_return_pct > self.min_sol_move_pct and avg_btc_return_pct < self.max_btc_lag_pct:
            # Add a price-based confirmation: current BTC close is above its 20-bar SMA
            # This helps avoid catching falling knives or false signals
            closes = ctx.closes(20)
            if len(closes) >= 20:
                sma20 = self._sma(closes, 20)
                if sma20 is not None and ctx.bar.close > sma20:
                    signal_direction = "long"
                    confidence = min(0.8, (avg_sol_return_pct / self.min_sol_move_pct) * 0.5 + (1 - avg_btc_return_pct / self.max_btc_lag_pct) * 0.3)
                    reason = "sol_leading_btc_lagging_sma_confirm"
            
        # Short condition: SOL makes a significant negative move, BTC lags behind
        elif avg_sol_return_pct < -self.min_sol_move_pct and avg_btc_return_pct > -self.max_btc_lag_pct:
            # Add a price-based confirmation: current BTC close is below its 20-bar SMA
            closes = ctx.closes(20)
            if len(closes) >= 20:
                sma20 = self._sma(closes, 20)
                if sma20 is not None and ctx.bar.close < sma20:
                    signal_direction = "short"
                    confidence = min(0.8, (abs(avg_sol_return_pct) / self.min_sol_move_pct) * 0.5 + (1 - abs(avg_btc_return_pct) / self.max_btc_lag_pct) * 0.3)
                    reason = "sol_lagging_btc_leading_sma_confirm"

        if ctx.has_position():
            # If SOL and BTC returns have normalized, consider exiting
            if ctx.position_direction() == "long":
                if current_sol_return_pct < self.min_sol_move_pct * 0.5 and current_btc_return_pct > self.max_btc_lag_pct * 0.5:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "long_position_normalized_exit",
                        "current_sol_return_pct": current_sol_return_pct,
                        "current_btc_return_pct": current_btc_return_pct
                    })
            elif ctx.position_direction() == "short":
                if current_sol_return_pct > -self.min_sol_move_pct * 0.5 and current_btc_return_pct < -self.max_btc_lag_pct * 0.5:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "short_position_normalized_exit",
                        "current_sol_return_pct": current_sol_return_pct,
                        "current_btc_return_pct": current_btc_return_pct
                    })
            return None # Hold current position
        else: # No position
            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "avg_sol_return_pct": avg_sol_return_pct,
                        "avg_btc_return_pct": avg_btc_return_pct,
                        "current_close": ctx.bar.close,
                        "sma20": sma20 if 'sma20' in locals() else None
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, regardless of whether it was a stop, TP, or signal
        self.last_trade_bar = ctx.bar_index
        # Optional: Adaptive sizing logic could go here based on position["return_bps"]
        pass