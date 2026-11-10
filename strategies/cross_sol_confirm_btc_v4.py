from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirmation(Strategy):
    METADATA = {
        "name": "Cross Asset Trend Confirmation",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour average hold
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.trend_period = 20 # For EMA and trend confirmation
        self.confirmation_period = 3 # How many bars for confirmation
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.cooldown_bars

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

        closes = ctx.closes(self.trend_period + self.confirmation_period)
        if len(closes) < self.trend_period + self.confirmation_period:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Market Regime Filter: Only trade in normal or volatile regimes
        # And avoid crisis
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None

        # Leader (SOL) trend agreement
        # Use recent SOL returns to gauge short-term trend
        sol_returns = []
        for i in range(self.confirmation_period):
            sol_returns.append(ctx.features.get("sol_return_pct", 0.0, offset=i))

        # Target (BTC) trend confirmation
        btc_returns = []
        for i in range(self.confirmation_period):
            btc_returns.append(ctx.features.get("btc_return_pct", 0.0, offset=i))

        # Short-term EMA for BTC to confirm local trend
        btc_ema = self._ema(closes, self.trend_period)
        if btc_ema is None:
            return None

        current_btc_close = closes[-1]
        previous_btc_close = closes[-2] if len(closes) >= 2 else current_btc_close

        # Check for existing position
        if ctx.has_position():
            # Exit if the cross-asset relationship normalizes or reverses
            # Or if BTC price crosses back over the EMA against the trend
            if ctx.position_direction() == "long":
                # If SOL returns become negative or BTC crosses below EMA
                if any(r < -0.1 for r in sol_returns[-self.confirmation_period:]) or current_btc_close < btc_ema:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_exit_sol_reversal_or_btc_ema_cross",
                        "sol_returns_last_N": sol_returns[-self.confirmation_period:],
                        "btc_close": current_btc_close,
                        "btc_ema": btc_ema,
                        "market_regime": ctx.market.get("regime")
                    })
            elif ctx.position_direction() == "short":
                # If SOL returns become positive or BTC crosses above EMA
                if any(r > 0.1 for r in sol_returns[-self.confirmation_period:]) or current_btc_close > btc_ema:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_exit_sol_reversal_or_btc_ema_cross",
                        "sol_returns_last_N": sol_returns[-self.confirmation_period:],
                        "btc_close": current_btc_close,
                        "btc_ema": btc_ema,
                        "market_regime": ctx.market.get("regime")
                    })
            return None # Hold position if no exit condition met

        # Entry logic
        # Long entry: SOL showing positive momentum, BTC confirming, and price above EMA
        if all(r > 0.2 for r in sol_returns[-self.confirmation_period:]) and \
           all(r > 0.0 for r in btc_returns[-self.confirmation_period:]) and \
           current_btc_close > btc_ema and \
           previous_btc_close <= btc_ema: # Only trigger on EMA cross-over
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.75, metadata={
                "reason": "cross_asset_long_sol_driving_btc_ema_cross",
                "sol_returns_last_N": sol_returns[-self.confirmation_period:],
                "btc_returns_last_N": btc_returns[-self.confirmation_period:],
                "btc_close": current_btc_close,
                "btc_ema": btc_ema,
                "market_regime": ctx.market.get("regime")
            })

        # Short entry: SOL showing negative momentum, BTC confirming, and price below EMA
        if all(r < -0.2 for r in sol_returns[-self.confirmation_period:]) and \
           all(r < 0.0 for r in btc_returns[-self.confirmation_period:]) and \
           current_btc_close < btc_ema and \
           previous_btc_close >= btc_ema: # Only trigger on EMA cross-under
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.75, metadata={
                "reason": "cross_asset_short_sol_driving_btc_ema_cross",
                "sol_returns_last_N": sol_returns[-self.confirmation_period:],
                "btc_returns_last_N": btc_returns[-self.confirmation_period:],
                "btc_close": current_btc_close,
                "btc_ema": btc_ema,
                "market_regime": ctx.market.get("regime")
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already set last_trade_bar on signal creation
        pass