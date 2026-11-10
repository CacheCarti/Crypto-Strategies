from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthCorrelationTrend(Strategy):
    METADATA = {
        "name": "SolEthCorrelationTrend",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.eth_lookback = 3 # Lookback for ETH return
        self.sol_lookback = 3 # Lookback for SOL return
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.eth_lookback)]
        sol_returns = [ctx.features.get("sol_return_pct", 0.0) for _ in range(self.sol_lookback)]

        # Ensure we have enough data for recent returns
        if not eth_returns or not sol_returns:
            return None

        # Use the most recent return for simplicity, or average for smoothness
        current_eth_return = eth_returns[-1]
        current_sol_return = sol_returns[-1]

        # Trend agreement: ETH leads, SOL confirms
        # Long condition: ETH is positive, SOL is positive
        # Short condition: ETH is negative, SOL is negative
        eth_trend_threshold = 0.15 # % return
        sol_confirm_threshold = 0.10 # % return

        signal_direction = None
        reason = ""
        confidence = 0.0

        # Check if we have an open position and need to consider exiting
        if ctx.has_position():
            position = ctx.position
            # Exit if the leader (ETH) disagrees with our position direction
            if position["direction"] == "long" and current_eth_return < -eth_trend_threshold / 2:
                signal_direction = "flat"
                reason = "eth_leader_disagreement_exit_long"
                confidence = 0.6
            elif position["direction"] == "short" and current_eth_return > eth_trend_threshold / 2:
                signal_direction = "flat"
                reason = "eth_leader_disagreement_exit_short"
                confidence = 0.6
            
            # If no exit signal yet, check for normalization (returns flatten out)
            if signal_direction is None:
                if abs(current_eth_return) < eth_trend_threshold / 2 and abs(current_sol_return) < sol_confirm_threshold / 2:
                    signal_direction = "flat"
                    reason = "trend_normalization_exit"
                    confidence = 0.5
            
            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "eth_return": current_eth_return,
                        "sol_return": current_sol_return,
                        "position_direction": position["direction"]
                    }
                )
            
            # If already in position and no exit condition met, just hold
            return None

        # Entry logic
        # Long entry
        if current_eth_return > eth_trend_threshold and current_sol_return > sol_confirm_threshold:
            signal_direction = "long"
            reason = "eth_leader_bull_sol_confirms"
            confidence = min(0.7, 0.4 + (current_eth_return + current_sol_return) / 2.0) # Scale confidence
        # Short entry
        elif current_eth_return < -eth_trend_threshold and current_sol_return < -sol_confirm_threshold:
            signal_direction = "short"
            reason = "eth_leader_bear_sol_confirms"
            confidence = min(0.7, 0.4 + (-current_eth_return - current_sol_return) / 2.0) # Scale confidence

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
                    "eth_return": current_eth_return,
                    "sol_return": current_sol_return,
                    "eth_trend_threshold": eth_trend_threshold,
                    "sol_confirm_threshold": sol_confirm_threshold
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already update last_trade_bar on signal generation for flat signals.
        # This is for potential future adaptive logic based on position outcomes.
        pass