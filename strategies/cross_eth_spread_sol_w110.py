from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class CrossAssetSpreadFade(Strategy):
    METADATA = {
        "name": "Cross Asset Spread Fade (SOL-ETH)",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 3, # Aim for a few hours hold
        "warmup_bars": 60, # Need enough data for Z-score and EMAs
        "required_features": ["eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 30 # Period for calculating the spread's z-score
        self.zscore_threshold = 1.5 # Z-score deviation to consider extreme
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # Use a deque to efficiently store historical spreads for z-score calculation
        self.spread_history = collections.deque(maxlen=self.spread_period * 2) # Store enough for stddev calculation

        # EMA periods for filtering "freshness" of returns
        self.fast_ema_period = 5
        self.slow_ema_period = 20

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

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar <= self.cooldown_bars:
            return None

        sol_returns = ctx.closes(self.spread_period + 1)
        eth_returns_pct = ctx.features.get("eth_return_pct", 0.0)

        if len(sol_returns) < self.spread_period + 1:
            return None

        # Calculate SOL's 1-period return as a proxy for its immediate "return_pct"
        sol_current_return = (sol_returns[-1] / sol_returns[-2] - 1) * 100 if sol_returns[-2] != 0 else 0.0

        # Spread: SOL's return minus ETH's return
        # A positive spread means SOL outperformed ETH, negative means SOL underperformed ETH
        current_spread = sol_current_return - eth_returns_pct
        self.spread_history.append(current_spread)

        if len(self.spread_history) < self.spread_period:
            return None

        spread_zscore = self._zscore(list(self.spread_history), self.spread_period)

        # Freshness filter: check if ETH returns are "fresh" (not stagnant)
        # We use a proxy for "freshness" by comparing a fast EMA of ETH returns to a slow EMA.
        # If the fast EMA is significantly different from the slow EMA, it suggests recent movement.
        # This is a bit of an approximation for "causal driver freshness"
        eth_return_history = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.slow_ema_period)] # Dummy for EMA
        # In a real scenario, we'd need a history of eth_return_pct, but ctx.features.get only gives current.
        # For this example, we'll assume the eth_return_pct has some recent volatility if the absolute value is above a threshold.
        # A more robust "freshness" would require storing historical eth_return_pct values.
        # For now, let's use a simple absolute value check.
        eth_freshness_threshold = 0.05 # e.g., 0.05% move
        is_eth_fresh = abs(eth_returns_pct) > eth_freshness_threshold

        if ctx.has_position():
            # Exit condition: if spread normalizes (z-score close to zero)
            if abs(spread_zscore) < 0.5: # If z-score is close to neutral
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_normalized",
                    "current_spread": current_spread,
                    "spread_zscore": spread_zscore,
                    "sol_return": sol_current_return,
                    "eth_return_pct": eth_returns_pct,
                })
            return None # Hold position if conditions not met

        # Entry logic: Fade extreme spread dislocations
        if spread_zscore > self.zscore_threshold and is_eth_fresh: # SOL outperformed ETH significantly, fade by shorting SOL
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "extreme_sol_outperformance_fade",
                    "current_spread": current_spread,
                    "spread_zscore": spread_zscore,
                    "sol_return": sol_current_return,
                    "eth_return_pct": eth_returns_pct,
                    "is_eth_fresh": is_eth_fresh,
                }
            )
        elif spread_zscore < -self.zscore_threshold and is_eth_fresh: # SOL underperformed ETH significantly, fade by longing SOL
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "extreme_sol_underperformance_fade",
                    "current_spread": current_spread,
                    "spread_zscore": spread_zscore,
                    "sol_return": sol_current_return,
                    "eth_return_pct": eth_returns_pct,
                    "is_eth_fresh": is_eth_fresh,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy yet, but could be added here
        pass