from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirm(Strategy):
    METADATA = {
        "name": "CrossAssetTrendConfirm",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6 hours
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars # Allow trading from start

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        # Calculate EMA for BTC
        btc_ema = self._ema(closes, self.ema_period)
        if btc_ema is None:
            return None

        current_btc_close = closes[-1]
        previous_btc_close = closes[-2] if len(closes) > 1 else current_btc_close

        # Get cross-asset returns
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)

        # Get market regime
        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")

        # Exit logic
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit if BTC price falls below EMA, or if ETH returns turn negative
                if current_btc_close < btc_ema and previous_btc_close >= btc_ema:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "btc_ema_cross_down_exit", "current_price": current_btc_close, "ema": btc_ema, "eth_return_pct": eth_return_pct})
                if eth_return_pct < -0.1: # Significant negative ETH return as exit signal
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "eth_leader_negative_exit", "current_price": current_btc_close, "eth_return_pct": eth_return_pct})
            elif ctx.position_direction() == "short":
                # Exit if BTC price rises above EMA, or if ETH returns turn positive
                if current_btc_close > btc_ema and previous_btc_close <= btc_ema:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "btc_ema_cross_up_exit", "current_price": current_btc_close, "ema": btc_ema, "eth_return_pct": eth_return_pct})
                if eth_return_pct > 0.1: # Significant positive ETH return as exit signal
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "eth_leader_positive_exit", "current_price": current_btc_close, "eth_return_pct": eth_return_pct})
            return None # Hold position if no exit condition met

        # Entry logic
        # Require positive ETH return (leader) and BTC price above its EMA (target confirmation) for long
        long_condition = (
            eth_return_pct > 0.2  # ETH shows positive momentum
            and btc_return_pct > 0.1 # BTC also shows positive momentum
            and current_btc_close > btc_ema # BTC price confirms trend
            and previous_btc_close <= btc_ema # Entry on EMA cross-up
            and trend_regime == "bull" # Only enter long in a bull trend
            and regime == "NORMAL" # Avoid volatile regimes for entry
        )

        # Require negative ETH return (leader) and BTC price below its EMA (target confirmation) for short
        short_condition = (
            eth_return_pct < -0.2 # ETH shows negative momentum
            and btc_return_pct < -0.1 # BTC also shows negative momentum
            and current_btc_close < btc_ema # BTC price confirms trend
            and previous_btc_close >= btc_ema # Entry on EMA cross-down
            and trend_regime == "bear" # Only enter short in a bear trend
            and regime == "NORMAL" # Avoid volatile regimes for entry
        )

        if long_condition:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.75, metadata={
                "reason": "cross_asset_eth_leader_btc_confirm_long",
                "eth_return_pct": eth_return_pct,
                "btc_return_pct": btc_return_pct,
                "current_price": current_btc_close,
                "ema": btc_ema,
                "trend_regime": trend_regime,
                "market_regime": regime
            })

        if short_condition:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.75, metadata={
                "reason": "cross_asset_eth_leader_btc_confirm_short",
                "eth_return_pct": eth_return_pct,
                "btc_return_pct": btc_return_pct,
                "current_price": current_btc_close,
                "ema": btc_ema,
                "trend_regime": trend_regime,
                "market_regime": regime
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive sizing
        pass