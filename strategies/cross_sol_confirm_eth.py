from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthCrossAssetTrend(Strategy):
    METADATA = {
        "name": "SolEthCrossAssetTrend",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # 2 hours
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.sol_trend_period = 10
        self.eth_trend_period = 10
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.eth_trend_period + 1)
        sol_returns = [ctx.features.get("sol_return_pct", 0.0)]
        eth_returns = [ctx.features.get("eth_return_pct", 0.0)]

        if len(closes) < self.eth_trend_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Get recent returns for 'freshness'
        # For sol_return_pct and eth_return_pct, we assume they are already recent
        # A simple way to check 'freshness' is to see if the return is significant
        
        current_sol_return = ctx.features.get("sol_return_pct", 0.0)
        current_eth_return = ctx.features.get("eth_return_pct", 0.0)

        # Define thresholds for significant returns
        return_threshold = 0.005 # 0.5% return in the last period

        # Check for active trends in both assets
        sol_is_up_trending = current_sol_return > return_threshold
        sol_is_down_trending = current_sol_return < -return_threshold
        eth_is_up_trending = current_eth_return > return_threshold
        eth_is_down_trending = current_eth_return < -return_threshold

        # Close price trend for confirmation
        eth_price_change = closes[-1] - closes[-2]
        eth_price_up = eth_price_change > 0
        eth_price_down = eth_price_change < 0

        signal_direction = None
        signal_confidence = 0.0
        signal_reason = ""
        signal_metadata = {
            "sol_return": current_sol_return,
            "eth_return": current_eth_return,
            "eth_price_change": eth_price_change
        }

        if not ctx.has_position():
            # Long entry condition: SOL leads up, ETH confirms up, ETH price confirms
            if sol_is_up_trending and eth_is_up_trending and eth_price_up:
                signal_direction = "long"
                signal_confidence = 0.7
                signal_reason = "sol_lead_eth_confirm_bullish"
                self.last_trade_bar = ctx.bar_index

            # Short entry condition: SOL leads down, ETH confirms down, ETH price confirms
            elif sol_is_down_trending and eth_is_down_trending and eth_price_down:
                signal_direction = "short"
                signal_confidence = 0.7
                signal_reason = "sol_lead_eth_confirm_bearish"
                self.last_trade_bar = ctx.bar_index
        else:
            # Exit condition: relationship normalizes or leader disagrees
            if ctx.position_direction() == "long":
                # Exit if SOL turns neutral/down or ETH turns neutral/down
                if not sol_is_up_trending or not eth_is_up_trending:
                    signal_direction = "flat"
                    signal_confidence = 0.6
                    signal_reason = "long_exit_relationship_normalizing"
                    self.last_trade_bar = ctx.bar_index
            elif ctx.position_direction() == "short":
                # Exit if SOL turns neutral/up or ETH turns neutral/up
                if not sol_is_down_trending or not eth_is_down_trending:
                    signal_direction = "flat"
                    signal_confidence = 0.6
                    signal_reason = "short_exit_relationship_normalizing"
                    self.last_trade_bar = ctx.bar_index

        if signal_direction:
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={**signal_metadata, "reason": signal_reason}
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just reset last trade bar for cooldown
        pass