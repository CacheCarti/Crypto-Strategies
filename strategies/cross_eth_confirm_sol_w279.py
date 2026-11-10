from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthCorrelationTrade(Strategy):
    METADATA = {
        "name": "SolEthCorrelationTrade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.eth_lookback_period = 5
        self.sol_lookback_period = 5
        self.min_correlation = 0.7
        self.max_divergence_pct = 0.005 # 0.5%
        self.cooldown_bars = 10
        self.last_trade_bar = -self.cooldown_bars

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

    def _correlation(self, series1, series2, period):
        if len(series1) < period or len(series2) < period:
            return None
        s1 = series1[-period:]
        s2 = series2[-period:]
        
        mean1 = sum(s1) / period
        mean2 = sum(s2) / period

        std1 = self._stddev(s1, period)
        std2 = self._stddev(s2, period)

        if std1 == 0 or std2 == 0:
            return 0.0

        covariance = sum((x - mean1) * (y - mean2) for x, y in zip(s1, s2)) / period
        return covariance / (std1 * std2)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.eth_lookback_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        eth_returns = [ctx.features.get("eth_return_pct", 0.0)]
        sol_returns = [ctx.features.get("sol_return_pct", 0.0)]
        
        # We need recent returns, not just the current bar's 24h return.
        # Since we only get current bar's 24h return, we simulate short-term momentum
        # by looking at price action relative to a short EMA.
        
        # Simple momentum check for SOL
        sol_closes = ctx.closes(self.sol_lookback_period + 1)
        if len(sol_closes) < self.sol_lookback_period + 1:
            return None
        sol_momentum = (sol_closes[-1] - sol_closes[-2]) / sol_closes[-2] if sol_closes[-2] != 0 else 0.0

        # Simple momentum check for ETH
        eth_closes = ctx.closes(self.eth_lookback_period + 1)
        if len(eth_closes) < self.eth_lookback_period + 1:
            return None
        eth_momentum = (eth_closes[-1] - eth_closes[-2]) / eth_closes[-2] if eth_closes[-2] != 0 else 0.0

        # Correlation between short-term price changes (not the 24h feature)
        # This is a proxy for the user's intent of "freshness" in relationship
        # rather than using the potentially stale 24h return feature for correlation.
        if len(sol_closes) < self.sol_lookback_period + 1 or len(eth_closes) < self.eth_lookback_period + 1:
            return None
            
        sol_price_changes = [(sol_closes[i] - sol_closes[i-1])/sol_closes[i-1] for i in range(1, len(sol_closes))]
        eth_price_changes = [(eth_closes[i] - eth_closes[i-1])/eth_closes[i-1] for i in range(1, len(eth_closes))]

        if len(sol_price_changes) < self.sol_lookback_period or len(eth_price_changes) < self.eth_lookback_period:
            return None

        current_correlation = self._correlation(eth_price_changes, sol_price_changes, min(len(eth_price_changes), len(sol_price_changes)))
        
        if current_correlation is None:
            return None

        if ctx.has_position():
            # Exit condition: correlation normalizes or divergence closes
            position = ctx.position
            entry_price = position["entry_price"]
            current_price = ctx.bar.close

            if position["direction"] == "long":
                # If SOL goes up faster than ETH was expected, or correlation breaks down
                # Or if SOL momentum reverses strongly
                if current_correlation < self.min_correlation * 0.9 or sol_momentum < -self.max_divergence_pct * 0.5:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_exit_correlation_breakdown_or_sol_reversal",
                        "correlation": current_correlation,
                        "sol_momentum": sol_momentum,
                        "eth_momentum": eth_momentum
                    })
            elif position["direction"] == "short":
                # If SOL goes down faster than ETH was expected, or correlation breaks down
                # Or if SOL momentum reverses strongly
                if current_correlation < self.min_correlation * 0.9 or sol_momentum > self.max_divergence_pct * 0.5:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_exit_correlation_breakdown_or_sol_reversal",
                        "correlation": current_correlation,
                        "sol_momentum": sol_momentum,
                        "eth_momentum": eth_momentum
                    })
            return None

        # Entry logic: High correlation, ETH leads, SOL lags
        if current_correlation > self.min_correlation:
            # ETH shows strong recent move, SOL has not yet caught up
            # Long SOL if ETH is strongly positive and SOL is not as positive (or even slightly negative)
            if eth_momentum > self.max_divergence_pct and sol_momentum < eth_momentum * 0.5: # ETH up, SOL lagging
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.75, metadata={
                    "reason": "eth_leads_sol_lags_long",
                    "correlation": current_correlation,
                    "eth_momentum": eth_momentum,
                    "sol_momentum": sol_momentum
                })

            # Short SOL if ETH is strongly negative and SOL is not as negative (or even slightly positive)
            elif eth_momentum < -self.max_divergence_pct and sol_momentum > eth_momentum * 0.5: # ETH down, SOL lagging
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.75, metadata={
                    "reason": "eth_leads_sol_lags_short",
                    "correlation": current_correlation,
                    "eth_momentum": eth_momentum,
                    "sol_momentum": sol_momentum
                })
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass