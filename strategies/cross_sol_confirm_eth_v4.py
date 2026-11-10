from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirmation(Strategy):
    METADATA = {
        "name": "Cross Asset Trend Confirmation",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.trend_period = 20
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars
        self.min_confidence = 0.6
        self.max_confidence = 0.9

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.trend_period + 1)
        sol_returns = [ctx.features.get("sol_return_pct", 0.0) for _ in range(self.trend_period)]
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.trend_period)]

        if len(closes) < self.METADATA["warmup_bars"] or \
           len(sol_returns) < self.trend_period or \
           len(eth_returns) < self.trend_period:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_sol_return = ctx.features.get("sol_return_pct", 0.0)
        current_eth_return = ctx.features.get("eth_return_pct", 0.0)

        # Calculate Z-scores for recent price action
        eth_zscore = self._zscore(closes, self.trend_period)
        sol_zscore_returns = self._zscore(sol_returns, self.trend_period)
        eth_zscore_returns = self._zscore(eth_returns, self.trend_period)

        if eth_zscore is None or sol_zscore_returns is None or eth_zscore_returns is None:
            return None

        # Regime filter: only trade in 'normal' or 'volatile' conditions, avoid 'crisis'
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None

        # Trend filter: only trade if the market trend is not strongly against our direction
        market_trend = ctx.market.get("trend_regime", "neutral")

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Long entry conditions:
        # 1. SOL shows strong positive momentum (leader)
        # 2. ETH shows positive momentum (target confirmation)
        # 3. ETH price is not extremely overbought (Z-score not too high)
        if current_sol_return > 0.5 and current_eth_return > 0.2 and eth_zscore < 1.5:
            if sol_zscore_returns > 0.5 and eth_zscore_returns > 0.2: # Z-scores of returns confirm
                if market_trend in ["bull", "neutral"]: # Don't long into strong bear market
                    signal_direction = "long"
                    confidence = min(self.max_confidence, self.min_confidence + (current_sol_return + current_eth_return) / 5.0)
                    reason = "sol_eth_positive_momentum_confirmation"

        # Short entry conditions:
        # 1. SOL shows strong negative momentum (leader)
        # 2. ETH shows negative momentum (target confirmation)
        # 3. ETH price is not extremely oversold (Z-score not too low)
        elif current_sol_return < -0.5 and current_eth_return < -0.2 and eth_zscore > -1.5:
            if sol_zscore_returns < -0.5 and eth_zscore_returns < -0.2: # Z-scores of returns confirm
                if market_trend in ["bear", "neutral"]: # Don't short into strong bull market
                    signal_direction = "short"
                    confidence = min(self.max_confidence, self.min_confidence + (-current_sol_return - current_eth_return) / 5.0)
                    reason = "sol_eth_negative_momentum_confirmation"

        # Exit conditions (normalize or time stop)
        if ctx.has_position():
            pos_direction = ctx.position_direction()
            pos_entry_price = ctx.position["entry_price"]

            # Check for normalization of cross-asset relationship
            # If SOL and ETH returns diverge or become neutral, consider exiting
            if pos_direction == "long":
                if current_sol_return < 0.1 or current_eth_return < 0.0 or eth_zscore_returns < 0.0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_position_sol_eth_momentum_loss",
                        "current_sol_return": current_sol_return,
                        "current_eth_return": current_eth_return,
                        "eth_zscore_returns": eth_zscore_returns
                    })
            elif pos_direction == "short":
                if current_sol_return > -0.1 or current_eth_return > 0.0 or eth_zscore_returns > 0.0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_position_sol_eth_momentum_loss",
                        "current_sol_return": current_sol_return,
                        "current_eth_return": current_eth_return,
                        "eth_zscore_returns": eth_zscore_returns
                    })
            return None # Hold current position if no exit signal

        if signal_direction and confidence >= self.min_confidence:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "current_sol_return": current_sol_return,
                    "current_eth_return": current_eth_return,
                    "eth_zscore_price": eth_zscore,
                    "sol_zscore_returns": sol_zscore_returns,
                    "eth_zscore_returns": eth_zscore_returns,
                    "market_regime": ctx.market.get("regime", "NORMAL"),
                    "market_trend": market_trend
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy currently, just logging
        pass