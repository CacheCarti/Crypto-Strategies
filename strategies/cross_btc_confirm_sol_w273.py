from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirmation(Strategy):
    METADATA = {
        "name": "Cross Asset Trend Confirmation SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for 4-hour hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_trend_period = 12  # Bars for BTC trend
        self.sol_trend_period = 6   # Bars for SOL trend
        self.min_btc_return_pct = 0.5  # Minimum BTC 1h return % to consider a trend
        self.min_sol_return_pct = 0.3  # Minimum SOL 1h return % to confirm
        self.cooldown_bars = 10     # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_btc_return = ctx.features.get("btc_return_pct", 0.0)
        current_sol_return = ctx.features.get("sol_return_pct", 0.0)

        # Calculate short-term trend for BTC and SOL using recent returns
        btc_returns_history = [ctx.features.get("btc_return_pct", 0.0, offset=-i) for i in range(self.btc_trend_period)]
        sol_returns_history = [ctx.features.get("sol_return_pct", 0.0, offset=-i) for i in range(self.sol_trend_period)]

        if len(btc_returns_history) < self.btc_trend_period or len(sol_returns_history) < self.sol_trend_period:
            return None

        avg_btc_return = sum(btc_returns_history) / self.btc_trend_period
        avg_sol_return = sum(sol_returns_history) / self.sol_trend_period

        # Entry logic
        if not ctx.has_position():
            # Long condition: BTC strong positive trend, SOL confirms positive
            if avg_btc_return > self.min_btc_return_pct and avg_sol_return > self.min_sol_return_pct:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=min(1.0, (avg_btc_return + avg_sol_return) / (self.min_btc_return_pct + self.min_sol_return_pct) * 0.7),
                    metadata={
                        "reason": "btc_sol_positive_trend_confirmation",
                        "avg_btc_return": avg_btc_return,
                        "avg_sol_return": avg_sol_return,
                        "current_btc_return": current_btc_return,
                        "current_sol_return": current_sol_return,
                    }
                )

            # Short condition: BTC strong negative trend, SOL confirms negative
            if avg_btc_return < -self.min_btc_return_pct and avg_sol_return < -self.min_sol_return_pct:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=min(1.0, (abs(avg_btc_return) + abs(avg_sol_return)) / (self.min_btc_return_pct + self.min_sol_return_pct) * 0.7),
                    metadata={
                        "reason": "btc_sol_negative_trend_confirmation",
                        "avg_btc_return": avg_btc_return,
                        "avg_sol_return": avg_sol_return,
                        "current_btc_return": current_btc_return,
                        "current_sol_return": current_sol_return,
                    }
                )

        # Exit logic
        elif ctx.has_position():
            # Get current position details
            position_direction = ctx.position_direction()
            
            # Exit if leader (BTC) disagrees with current position
            if position_direction == "long" and avg_btc_return < 0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "btc_leader_disagreement_long_exit",
                        "avg_btc_return": avg_btc_return,
                        "avg_sol_return": avg_sol_return,
                    }
                )
            elif position_direction == "short" and avg_btc_return > 0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "btc_leader_disagreement_short_exit",
                        "avg_btc_return": avg_btc_return,
                        "avg_sol_return": avg_sol_return,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar for cooldown
        self.last_trade_bar = ctx.bar_index
        # Optional: Log position details or adapt strategy based on performance
        # For instance, if the strategy were adaptive, you might adjust parameters here.