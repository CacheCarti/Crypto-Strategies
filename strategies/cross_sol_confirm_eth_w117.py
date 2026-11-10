from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirm(Strategy):
    METADATA = {
        "name": "Cross Asset Trend Confirm ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # ~3 hours
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars
        self.ema_period = 20 # For ETH price confirmation
        self.return_period = 4 # For recent returns
        self.min_return_magnitude = 0.005 # 0.5% return for confirmation

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

        closes = ctx.closes(self.ema_period + 1) # Need one more for diff
        if len(closes) < self.ema_period + 1:
            return None

        current_price = ctx.bar.close
        eth_ema = self._ema(closes, self.ema_period)
        if eth_ema is None:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        sol_returns = [ctx.features.get("sol_return_pct", 0.0)]
        eth_returns = [ctx.features.get("eth_return_pct", 0.0)]

        # Check recent returns for "freshness"
        # We're looking for strong, recent directional movement
        # Note: ctx.features.get("feature", default) gets the value for the CURRENT bar.
        # To get historical feature values, we would need a mechanism to store them,
        # which is not directly available via ctx.features.get() for past bars.
        # So we'll rely on the current bar's return for simplicity and to avoid
        # storing long histories of feature data, which can exceed memory limits.
        
        sol_recent_return = sol_returns[0]
        eth_recent_return = eth_returns[0]

        # Trend agreement and confirmation logic
        signal_direction = None
        confidence = 0.0
        reason_metadata = {}

        # Long condition: SOL shows strong positive return, ETH confirms with positive return and price above EMA
        if sol_recent_return > self.min_return_magnitude and eth_recent_return > self.min_return_magnitude:
            if current_price > eth_ema:
                signal_direction = "long"
                confidence = min(0.7, abs(sol_recent_return) * 50 + abs(eth_recent_return) * 50) # Scale confidence
                reason_metadata = {
                    "reason": "cross_asset_bullish_confirm",
                    "sol_return_pct": sol_recent_return,
                    "eth_return_pct": eth_recent_return,
                    "eth_price": current_price,
                    "eth_ema": eth_ema,
                }
        # Short condition: SOL shows strong negative return, ETH confirms with negative return and price below EMA
        elif sol_recent_return < -self.min_return_magnitude and eth_recent_return < -self.min_return_magnitude:
            if current_price < eth_ema:
                signal_direction = "short"
                confidence = min(0.7, abs(sol_recent_return) * 50 + abs(eth_recent_return) * 50) # Scale confidence
                reason_metadata = {
                    "reason": "cross_asset_bearish_confirm",
                    "sol_return_pct": sol_recent_return,
                    "eth_return_pct": eth_recent_return,
                    "eth_price": current_price,
                    "eth_ema": eth_ema,
                }

        # Exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            entry_price = ctx.position["entry_price"]

            # Exit if the relationship normalizes or reverses
            if pos_dir == "long":
                if current_price < eth_ema or sol_recent_return < 0 or eth_recent_return < 0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "long_exit_relationship_normalizing",
                        "sol_return_pct": sol_recent_return,
                        "eth_return_pct": eth_recent_return,
                        "eth_price": current_price,
                        "eth_ema": eth_ema,
                        "entry_price": entry_price,
                    })
            elif pos_dir == "short":
                if current_price > eth_ema or sol_recent_return > 0 or eth_recent_return > 0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "short_exit_relationship_normalizing",
                        "sol_return_pct": sol_recent_return,
                        "eth_return_pct": eth_recent_return,
                        "eth_price": current_price,
                        "eth_ema": eth_ema,
                        "entry_price": entry_price,
                    })

            # If holding position and no exit signal, continue to hold
            return ctx.signal("hold", confidence=ctx.position["confidence"], metadata=ctx.position["metadata"])

        # Enter a new position
        if signal_direction and confidence > 0.5: # Require minimum confidence for entry
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=reason_metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for debugging/analysis, or adapt parameters
        pass