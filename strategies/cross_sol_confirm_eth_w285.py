from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirmation(Strategy):
    METADATA = {
        "name": "Cross Asset Trend Confirmation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Roughly 4 hours
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.trend_period = 20  # Period for EMA trend
        self.fast_ema_period = 10
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.trend_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown period after a trade
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        # Get relevant returns (causal driver: SOL, target: ETH)
        sol_returns = ctx.features.get("sol_return_pct", 0.0)
        eth_returns = ctx.features.get("eth_return_pct", 0.0)

        # Calculate EMAs for ETH price
        eth_fast_ema = self._ema(closes, self.fast_ema_period)
        eth_slow_ema = self._ema(closes, self.trend_period)

        if eth_fast_ema is None or eth_slow_ema is None:
            return None

        # Trend agreement conditions
        # Leader (SOL) shows strong positive return
        sol_bullish = sol_returns > 0.5  # Positive return threshold
        sol_bearish = sol_returns < -0.5 # Negative return threshold

        # Target (ETH) confirms with its own return and price action (EMA crossover)
        eth_bullish_confirmation = eth_returns > 0.2 and eth_fast_ema > eth_slow_ema and closes[-1] > closes[-2]
        eth_bearish_confirmation = eth_returns < -0.2 and eth_fast_ema < eth_slow_ema and closes[-1] < closes[-2]

        current_price = ctx.bar.close

        if ctx.has_position():
            # Exit logic: if leader disagrees, or ETH trend reverses
            if ctx.position_direction() == "long":
                if sol_bearish or eth_fast_ema < eth_slow_ema:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_exit_leader_disagrees_or_eth_trend_reverses",
                        "sol_return_pct": sol_returns,
                        "eth_return_pct": eth_returns,
                        "eth_fast_ema": eth_fast_ema,
                        "eth_slow_ema": eth_slow_ema,
                        "price": current_price
                    })
            elif ctx.position_direction() == "short":
                if sol_bullish or eth_fast_ema > eth_slow_ema:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_exit_leader_disagrees_or_eth_trend_reverses",
                        "sol_return_pct": sol_returns,
                        "eth_return_pct": eth_returns,
                        "eth_fast_ema": eth_fast_ema,
                        "eth_slow_ema": eth_slow_ema,
                        "price": current_price
                    })
            return None # Hold current position

        # Entry logic
        if sol_bullish and eth_bullish_confirmation:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.8, metadata={
                "reason": "cross_asset_long_sol_leads_eth_confirms",
                "sol_return_pct": sol_returns,
                "eth_return_pct": eth_returns,
                "eth_fast_ema": eth_fast_ema,
                "eth_slow_ema": eth_slow_ema,
                "price": current_price
            })
        elif sol_bearish and eth_bearish_confirmation:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.8, metadata={
                "reason": "cross_asset_short_sol_leads_eth_confirms",
                "sol_return_pct": sol_returns,
                "eth_return_pct": eth_returns,
                "eth_fast_ema": eth_fast_ema,
                "eth_slow_ema": eth_slow_ema,
                "price": current_price
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive sizing
        pass