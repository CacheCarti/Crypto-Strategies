from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirm(Strategy):
    METADATA = {
        "name": "Cross Asset Trend Confirm SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours hold time for 1h bars
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.eth_period = 3  # Lookback for ETH return agreement
        self.sol_period = 2  # Lookback for SOL return confirmation
        self.cooldown_bars = 5 # Cooldown after an exit
        self.last_signal_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        # Get recent returns for ETH and SOL
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.eth_period)]
        sol_returns = [ctx.features.get("sol_return_pct", 0.0) for _ in range(self.sol_period)]

        # Ensure we have fresh, non-zero returns
        if not all(r != 0.0 for r in eth_returns) or not all(r != 0.0 for r in sol_returns):
            return None

        # Causal driver: ETH return
        eth_mean_return = sum(eth_returns) / self.eth_period

        # Target confirmation: SOL return
        sol_mean_return = sum(sol_returns) / self.sol_period

        # Get 20-period SMA for SOL to confirm trend
        sma_20 = self._sma(closes, 20)
        if sma_20 is None:
            return None

        current_price = ctx.bar.close

        signal_direction = None
        confidence = 0.0
        reason = ""
        metadata = {}

        if not ctx.has_position():
            # Long condition: ETH strongly positive, SOL positive, and SOL above SMA
            if eth_mean_return > 0.05 and sol_mean_return > 0.01 and current_price > sma_20:
                signal_direction = "long"
                confidence = min(1.0, eth_mean_return * 5 + sol_mean_return * 10) # Scale confidence
                reason = "eth_leader_sol_target_uptrend_confirm"
                metadata = {
                    "eth_mean_return": eth_mean_return,
                    "sol_mean_return": sol_mean_return,
                    "current_price": current_price,
                    "sma_20": sma_20
                }
            # Short condition: ETH strongly negative, SOL negative, and SOL below SMA
            elif eth_mean_return < -0.05 and sol_mean_return < -0.01 and current_price < sma_20:
                signal_direction = "short"
                confidence = min(1.0, abs(eth_mean_return) * 5 + abs(sol_mean_return) * 10) # Scale confidence
                reason = "eth_leader_sol_target_downtrend_confirm"
                metadata = {
                    "eth_mean_return": eth_mean_return,
                    "sol_mean_return": sol_mean_return,
                    "current_price": current_price,
                    "sma_20": sma_20
                }
        else:
            # Exit condition: Leader (ETH) disagrees with current position OR target (SOL) crosses SMA
            if ctx.position_direction() == "long":
                if eth_mean_return < -0.02 or current_price < sma_20:
                    signal_direction = "flat"
                    reason = "eth_leader_disagrees_or_sol_below_sma_exit"
                    metadata = {
                        "eth_mean_return": eth_mean_return,
                        "current_price": current_price,
                        "sma_20": sma_20
                    }
            elif ctx.position_direction() == "short":
                if eth_mean_return > 0.02 or current_price > sma_20:
                    signal_direction = "flat"
                    reason = "eth_leader_disagrees_or_sol_above_sma_exit"
                    metadata = {
                        "eth_mean_return": eth_mean_return,
                        "current_price": current_price,
                        "sma_20": sma_20
                    }

        if signal_direction:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence if signal_direction != "flat" else 0.5, # Fixed confidence for exits
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just logging
        pass